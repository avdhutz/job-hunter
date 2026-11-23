"""Careers-site navigator agent (Sonnet + Playwright tools), traced in LangSmith.

Shared flow for every org:
  1. web search "<org> careers"  ->  open the official careers site
  2. (academic only) go to STAFF positions (unless the org has staff_section: false)
  3. apply the site's job-category filter (or keyword search)
  4. stop on the filtered list of postings (`finish`)
The agent only navigates. Reading the list and each posting is done by `extract.py` with a
cheap model, using hrefs from the DOM. After success the navigation is saved as a recipe
(steps, filtered-results URL, page markers) and later runs replay it with no agent; the agent
runs again only when the recipe breaks (or after 3 empty runs). If the agent fails too, the org
is set to `pending` and skipped. DB writes are not done here: `OrgResult.update` is applied by
persist_node, so dry runs stay read-only.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, ToolMessage
from langgraph.errors import GraphRecursionError
from langgraph.prebuilt import create_react_agent

from job_hunter import budget, config, db
from job_hunter.browser.extract import list_postings, read_posting
from job_hunter.browser.tools import OrgBrowser, RecipeError, build_tools, replay_recipe
from job_hunter.models import JobPosting, Sector

log = logging.getLogger(__name__)

INDUSTRY_STEPS = """\
1. Search the web for "{org} careers" and open the company's official careers site.
2. Use the site's job category / team filter for: {categories}. If there is no filter, use its keyword search.
3. When the page shows the filtered LIST of postings, call finish."""

ACADEMIC_STEPS = """\
1. Search the web for "{org} careers" and open the official careers / HR jobs site.
2. Go to STAFF positions (not faculty, postdoc or student jobs).
3. Use the job category filter for: {categories}.
4. When the page shows the filtered LIST of postings, call finish."""

ACADEMIC_NO_STAFF_STEPS = """\
1. Search the web for "{org} careers" and open the official careers site.
2. This organization has no separate Staff positions section: use its general job listing.
3. Use the job category filter for: {categories}.
4. When the page shows the filtered LIST of postings, call finish."""

SYSTEM = """You navigate a company or university careers website to reach the filtered list \
of job postings. Steps:
{steps}

Rules:
- Act only on elements by their [ref] number from the latest snapshot. Open only URLs that \
appeared in search results or snapshots; never guess a URL.
- Do not open individual postings and do not extract anything; another step reads the list.
- If a filter option you want does not exist, pick the closest one, or use the site's search box.
- Prefer few steps. If the site blocks you or has no jobs list, call finish with a note saying so."""


@dataclass
class OrgResult:
    postings: list[JobPosting] = field(default_factory=list)
    stats: dict = field(default_factory=dict)
    update: dict | None = None  # applied by persist_node via db.apply_org_update


def _steps(org: dict, sector: Sector, categories: list[str]) -> str:
    if sector == "industry":
        tmpl = INDUSTRY_STEPS
    else:
        tmpl = ACADEMIC_STEPS if org.get("staff_section", True) else ACADEMIC_NO_STAFF_STEPS
    return tmpl.format(org=org["name"], categories=", ".join(categories))


def _drop_thinking(m):
    """Editing old tool results invalidates the signatures on thinking blocks, so send none."""
    if isinstance(m, AIMessage) and isinstance(m.content, list):
        kept = [b for b in m.content
                if not (isinstance(b, dict) and b.get("type") in ("thinking", "redacted_thinking"))]
        return m.model_copy(update={"content": kept})
    return m


def _trim_history(state: dict) -> dict:
    """Snapshots are big and only the newest matters: blank older tool results so the cost
    of a long navigation doesn't grow with every step."""
    msgs = state["messages"]
    tool_idx = [i for i, m in enumerate(msgs) if isinstance(m, ToolMessage)]
    stale = set(tool_idx[:-2])
    out = [
        ToolMessage(content="[earlier page omitted]", tool_call_id=m.tool_call_id, name=m.name)
        if i in stale else _drop_thinking(m)
        for i, m in enumerate(msgs)
    ]
    return {"llm_input_messages": out}


def _build_agent(b: OrgBrowser, org: dict, sector: Sector, categories: list[str]):
    model = ChatAnthropic(
        model=config.settings()["models"]["agent"], max_tokens=2000,
        callbacks=[budget.UsageCallback()],
    )
    web_search = {"type": "web_search_20250305", "name": "web_search", "max_uses": 3}
    return create_react_agent(
        model, tools=[web_search, *build_tools(b)],
        prompt=SYSTEM.format(steps=_steps(org, sector, categories)),
        pre_model_hook=_trim_history,
    )


async def run_agent(b: OrgBrowser, org: dict, sector: Sector, categories: list[str]) -> None:
    """Navigate to the filtered results page. Raises if the agent gives up or runs out of steps."""
    agent = _build_agent(b, org, sector, categories)
    limit = config.settings()["agent_limits"]["max_steps_per_org"] * 2 + 1  # 2 supersteps/step
    try:
        await agent.ainvoke(
            {"messages": [("user", f"Go: {org['name']}")]},
            config={"recursion_limit": limit, "run_name": f"navigate {org['name']}",
                    "tags": [sector, org["name"]]},
        )
    except GraphRecursionError as e:
        raise RuntimeError(f"agent hit the step limit ({limit // 2} steps)") from e
    if not b.finished:
        raise RuntimeError("agent stopped without reaching a results page")


_agent_slots: dict[int, asyncio.Semaphore] = {}


def _slots() -> asyncio.Semaphore:
    """Agent concurrency cap, one semaphore per event loop."""
    loop = id(asyncio.get_running_loop())
    if loop not in _agent_slots:
        _agent_slots[loop] = asyncio.Semaphore(config.settings()["agent_limits"]["agent_concurrency"])
    return _agent_slots[loop]


async def navigate_org(
    org: dict, sector: Sector, categories: list[str], known: set[str] | None = None
) -> OrgResult:
    """Replay the saved recipe (or run the agent), then read the postings. Never raises."""
    name = org["name"]
    known = known or set()
    if budget.current().exceeded:
        return OrgResult(stats={"org": name, "skipped": "budget"})
    saved = db.get_org(name)
    if saved and saved["status"] == "ignored":
        return OrgResult(stats={"org": name, "skipped": "ignored"})

    recipe = json.loads(saved["recipe_json"]) if saved and saved["recipe_json"] else None
    if recipe and saved["zero_streak"] >= db.ZERO_STREAK_FORCE_AGENT and saved["last_nonzero_at"]:
        recipe = None  # quiet for 3 runs in a row but used to have postings: re-check for real

    update: dict = {"name": name, "sector": sector, "status": "active", "count": 0}
    try:
        async with _slots(), OrgBrowser.open() as b:
            mode = "recipe"
            if recipe:
                try:
                    await replay_recipe(b, recipe)
                except RecipeError as e:
                    log.info("%s: recipe failed (%s), running the agent", name, e)
                    recipe = None
            if not recipe:
                if budget.current().exceeded:
                    return OrgResult(stats={"org": name, "skipped": "budget"})
                mode = "agent"
                b.steps.clear()
                await run_agent(b, org, sector, categories)
                new = b.recipe(await b.make_markers())
                update["recipe_json"] = json.dumps(new)
                update["careers_url"] = new["careers_url"]

            items, hrefs, counts = await list_postings(b, known, sector)
            update["count"] = counts["listed"]
            postings = []
            for item, href in zip(items, hrefs, strict=True):
                if budget.current().exceeded:
                    counts["cut_short"] = True
                    break
                p = await read_posting(b, href, item, org, sector)
                if p:
                    postings.append(p)
            return OrgResult(postings, {"org": name, "mode": mode, **counts}, update)
    except Exception as e:  # noqa: BLE001 - per-org isolation: record it, carry on
        log.warning("%s failed: %s", name, e)
        update.update(status="pending")
        return OrgResult(stats={"org": name, "error": f"{type(e).__name__}: {e}"}, update=update)
