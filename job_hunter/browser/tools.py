"""Playwright layer for JS-heavy careers sites (Workday, PeopleAdmin, Taleo, custom pages).

`OrgBrowser` wraps one page for one org. `snapshot` returns visible text plus numbered
links/buttons/selects, so the model acts on refs and never has to write a URL. Every action is
recorded as a replayable step (a locator descriptor, not a ref), which is what recipes store.
"""

import re
from contextlib import asynccontextmanager

from langchain_core.tools import tool
from playwright.async_api import Page, async_playwright

from job_hunter import config

SNAPSHOT_JS = """
() => {
  document.querySelectorAll('[data-jh-ref]').forEach(e => e.removeAttribute('data-jh-ref'));
  const sel = 'a[href], button, select, input:not([type=hidden]), textarea, summary, ' +
              '[role=button], [role=link], [role=option], [role=combobox], [role=tab]';
  const tagIdx = {};
  const out = [];
  let n = 0;
  for (const el of document.querySelectorAll(sel)) {
    const r = el.getBoundingClientRect(), st = getComputedStyle(el);
    if ((r.width === 0 && r.height === 0) || st.visibility === 'hidden' || st.display === 'none') continue;
    const tag = el.tagName.toLowerCase();
    tagIdx[tag] = (tagIdx[tag] ?? -1) + 1;
    n += 1;
    el.setAttribute('data-jh-ref', n);
    const text = (el.innerText || el.getAttribute('aria-label') || el.placeholder || el.value || '')
      .replace(/\\s+/g, ' ').trim().slice(0, 100);
    out.push({
      ref: n, tag, type: el.type || null, text, id: el.id || null, name: el.getAttribute('name'),
      aria: el.getAttribute('aria-label'), href: tag === 'a' ? el.href : null, nth: tagIdx[tag],
      options: tag === 'select' ? [...el.options].map(o => o.text.trim()).slice(0, 40) : null,
    });
  }
  return { text: document.body ? document.body.innerText : '', elements: out };
}
"""

COUNT_RE = re.compile(r"\b\d[\d,]*\s+(?:results?|jobs?|positions?|openings?|postings?)\b", re.IGNORECASE)


class RecipeError(Exception):
    """A replayed step failed or the page lost its markers."""


class OrgBrowser:
    def __init__(self, page: Page):
        self.page = page
        self.steps: list[dict] = []
        self.elements: dict[int, dict] = {}
        self.finished = False
        self.finish_note = ""

    @classmethod
    @asynccontextmanager
    async def open(cls):
        """One headless browser + context per org: no shared state, nothing to clean up."""
        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                context = await browser.new_context()
                page = await context.new_page()
                page.set_default_timeout(15000)
                yield cls(page)
            finally:
                await browser.close()

    # --- reading ---------------------------------------------------------------------------

    async def _settle(self) -> None:
        try:
            await self.page.wait_for_load_state("domcontentloaded")
            await self.page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:  # noqa: BLE001 - busy pages never go idle; carry on
            pass
        await self.page.wait_for_timeout(500)

    async def read(self) -> dict:
        data = await self.page.evaluate(SNAPSHOT_JS)
        self.elements = {e["ref"]: e for e in data["elements"]}
        return data

    async def snapshot(self, max_chars: int = 3000, max_elements: int = 120) -> str:
        data = await self.read()
        lines = [f"URL: {self.page.url}", "", re.sub(r"\n{3,}", "\n\n", data["text"])[:max_chars],
                 "", "ELEMENTS:"]
        for e in data["elements"][:max_elements]:
            desc = f'[{e["ref"]}] {e["tag"]}'
            if e["type"] and e["tag"] == "input":
                desc += f'({e["type"]})'
            desc += f' "{e["text"]}"'
            if e["href"]:
                desc += f' -> {e["href"]}'
            if e["options"]:
                desc += " options=" + " | ".join(e["options"])
            lines.append(desc)
        if len(data["elements"]) > max_elements:
            lines.append(f"... {len(data['elements']) - max_elements} more elements not shown")
        return "\n".join(lines)

    async def page_text(self) -> str:
        return await self.page.evaluate("document.body ? document.body.innerText : ''")

    def href(self, ref: int) -> str | None:
        e = self.elements.get(ref)
        return e["href"] if e and e["tag"] == "a" else None

    # --- acting ----------------------------------------------------------------------------

    def _ref(self, ref: int):
        if ref not in self.elements:
            raise RecipeError(f"no element [{ref}] on the page; take a new snapshot")
        return self.page.locator(f'[data-jh-ref="{ref}"]'), self.elements[ref]

    @staticmethod
    def _descriptor(e: dict) -> dict:
        keys = ("tag", "text", "id", "name", "aria", "nth")
        return {k: e[k] for k in keys if e.get(k) not in (None, "")}

    @staticmethod
    def _locate(page: Page, d: dict):
        if d.get("id"):
            return page.locator(f'[id="{d["id"]}"]').first
        loc = page.locator(d["tag"])
        if d.get("text"):
            loc = loc.filter(has_text=d["text"])
        elif d.get("name"):
            return page.locator(f'{d["tag"]}[name="{d["name"]}"]').first
        elif d.get("aria"):
            return page.locator(f'{d["tag"]}[aria-label="{d["aria"]}"]').first
        else:
            return page.locator(d["tag"]).nth(d.get("nth", 0))
        return loc.first

    async def goto(self, url: str) -> None:
        await self.page.goto(url, wait_until="domcontentloaded")
        await self._settle()
        self.steps.append({"op": "goto", "url": url})

    async def click(self, ref: int) -> None:
        loc, e = self._ref(ref)
        await loc.click()
        await self._settle()
        self.steps.append({"op": "click", **self._descriptor(e)})

    async def fill(self, ref: int, text: str, press_enter: bool = False) -> None:
        loc, e = self._ref(ref)
        await loc.fill(text)
        if press_enter:
            await loc.press("Enter")
        await self._settle()
        self.steps.append({"op": "fill", "value": text, "enter": press_enter,
                           **self._descriptor(e)})

    async def select_option(self, ref: int, option: str) -> None:
        loc, e = self._ref(ref)
        await loc.select_option(label=option)
        await self._settle()
        self.steps.append({"op": "select", "option": option, **self._descriptor(e)})

    # --- recipes ---------------------------------------------------------------------------

    async def replay(self, steps: list[dict]) -> None:
        """Re-run recorded steps with no LLM. Any failure raises RecipeError."""
        try:
            for s in steps:
                if s["op"] == "goto":
                    await self.page.goto(s["url"], wait_until="domcontentloaded")
                    continue
                loc = self._locate(self.page, s)
                if s["op"] == "click":
                    await loc.click()
                elif s["op"] == "fill":
                    await loc.fill(s["value"])
                    if s.get("enter"):
                        await loc.press("Enter")
                elif s["op"] == "select":
                    await loc.select_option(label=s["option"])
                await self._settle()
        except RecipeError:
            raise
        except Exception as e:
            raise RecipeError(f"{type(e).__name__}: {str(e)[:200]}") from e
        await self._settle()

    async def make_markers(self) -> dict:
        """What the results page looks like when the filters worked: the labels of the options
        we picked (if still visible) and whether a "N results" element is shown."""
        text = (await self.page_text()).lower()
        picked = [s.get("option") or s.get("text") for s in self.steps
                  if s["op"] in ("click", "select")]
        return {
            "texts": sorted({t for t in picked if t and len(t) >= 3 and t.lower() in text}),
            "count": bool(COUNT_RE.search(text)),
        }

    async def markers_intact(self, markers: dict) -> bool:
        text = await self.page_text()
        low = text.lower()
        return all(t.lower() in low for t in markers.get("texts", [])) and (
            not markers.get("count") or bool(COUNT_RE.search(text)))

    def recipe(self, markers: dict) -> dict:
        gotos = [s["url"] for s in self.steps if s["op"] == "goto"]
        return {"careers_url": gotos[0] if gotos else None, "results_url": self.page.url,
                "steps": self.steps, "markers": markers}


async def replay_recipe(b: OrgBrowser, recipe: dict) -> None:
    """Try the saved results URL first (one step), then the full step list. Raises
    RecipeError if neither lands on a page whose markers are intact."""
    markers = recipe.get("markers", {})
    if recipe.get("results_url"):
        try:
            await b.page.goto(recipe["results_url"], wait_until="domcontentloaded")
            await b._settle()
            if await b.markers_intact(markers) and (markers.get("texts") or markers.get("count")):
                return
        except Exception:  # noqa: BLE001 - fall back to the steps
            pass
    await b.replay(recipe["steps"])
    if not await b.markers_intact(markers):
        raise RecipeError("results page lost its markers")


def build_tools(b: OrgBrowser) -> list:
    """LangChain tools for the navigator agent. Each action returns a fresh snapshot, so the
    model rarely needs a separate snapshot call."""
    limits = config.settings()["agent_limits"]

    async def view() -> str:
        return await b.snapshot(max_chars=limits["page_text_chars"] // 2, max_elements=120)

    async def guarded(coro) -> str:
        try:
            await coro
            return await view()
        except RecipeError as e:
            return f"ERROR: {e}"
        except Exception as e:  # noqa: BLE001 - tell the model, let it try something else
            return f"ERROR: {type(e).__name__}: {str(e)[:300]}"

    @tool
    async def goto(url: str) -> str:
        """Open a URL (use a URL you saw in search results or in a snapshot, never a guess)."""
        return await guarded(b.goto(url))

    @tool
    async def snapshot() -> str:
        """Visible text and numbered clickable elements of the current page."""
        return await view()

    @tool
    async def click(ref: int) -> str:
        """Click element [ref] from the latest snapshot."""
        return await guarded(b.click(ref))

    @tool
    async def fill(ref: int, text: str, press_enter: bool = False) -> str:
        """Type text into input [ref]; set press_enter to submit a search box."""
        return await guarded(b.fill(ref, text, press_enter))

    @tool
    async def select_option(ref: int, option: str) -> str:
        """Choose an option (by its visible label) in <select> [ref]."""
        return await guarded(b.select_option(ref, option))

    @tool(return_direct=True)
    async def finish(note: str) -> str:
        """Call when the current page is the filtered LIST of job postings (not a single
        posting). note: one line on what filter was applied."""
        b.finished, b.finish_note = True, note
        return "ok"

    return [goto, snapshot, click, fill, select_option, finish]
