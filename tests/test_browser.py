import json
import re
from pathlib import Path

import pytest

from job_hunter import budget, config, db
from job_hunter.browser import agent
from job_hunter.browser.extract import Detail, Listing, ListingItem, list_postings, read_posting
from job_hunter.browser.tools import OrgBrowser, RecipeError, replay_recipe

FIX = Path(__file__).parent / "fixtures"
URL = (FIX / "careers.html").as_uri()


@pytest.fixture(autouse=True)
def tmp_db(tmp_path, monkeypatch):
    s = dict(config.settings(), database=str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "settings", lambda: s)
    budget.start_run()


async def navigate(b: OrgBrowser) -> None:
    """What the agent would do on the fixture page."""
    await b.goto(URL)
    await b.snapshot()
    ref = next(r for r, e in b.elements.items() if e["tag"] == "select")
    await b.select_option(ref, "Data Analytics")
    ref = next(r for r, e in b.elements.items() if e["text"] == "Search")
    await b.click(ref)
    b.finished = True


class FakeListing:
    """Reads link refs out of the snapshot text, like the real model would."""
    async def ainvoke(self, messages):
        links = re.findall(r'\[(\d+)\] a "([^"]+)" -> ', messages[-1].content)
        return Listing(items=[ListingItem(ref=int(r), title=t) for r, t in links])


class FakeDetail:
    async def ainvoke(self, messages):
        return Detail(location="Austin, TX", posted_text="Posted 3 days ago")


async def test_snapshot_actions_and_recipe_replay():
    async with OrgBrowser.open() as b:
        await navigate(b)
        markers = await b.make_markers()
        recipe = b.recipe(markers)
    assert markers == {"texts": ["Data Analytics", "Search"], "count": True}
    assert [s["op"] for s in recipe["steps"]] == ["goto", "select", "click"]
    async with OrgBrowser.open() as b2:  # fresh browser, no LLM
        await replay_recipe(b2, json.loads(json.dumps(recipe)))
        assert await b2.markers_intact(recipe["markers"])


async def test_recipe_fails_when_site_changes():
    async with OrgBrowser.open() as b:
        await navigate(b)
        recipe = b.recipe(await b.make_markers())
    recipe["steps"][0]["url"] = (FIX / "careers_changed.html").as_uri()
    recipe["results_url"] = recipe["steps"][0]["url"]
    async with OrgBrowser.open() as b2:
        with pytest.raises(RecipeError):
            await replay_recipe(b2, recipe)


async def test_list_filters_titles_before_opening_pages():
    async with OrgBrowser.open() as b:
        await navigate(b)
        items, hrefs, counts = await list_postings(b, set(), "industry", FakeListing())
    assert [i.title for i in items] == ["Data Engineer II", "Data Analyst"]
    assert hrefs[0].endswith("job1.html")
    assert counts == {"listed": 4, "already_known": 0, "title_mismatch": 1, "seniority": 1,
                      "kept": 2}


async def test_read_posting_uses_visited_url():
    async with OrgBrowser.open() as b:
        await navigate(b)
        items, hrefs, _ = await list_postings(b, set(), "industry", FakeListing())
        p = await read_posting(b, hrefs[0], items[0], {"name": "Acme"}, "industry", FakeDetail())
    assert p.link.endswith("job1.html") and p.company == "Acme" and p.location == "Austin, TX"
    assert "Python and SQL" in p.description


async def test_navigate_org_agent_then_recipe(monkeypatch):
    calls = {"agent": 0}

    async def fake_agent(b, org, sector, categories):
        calls["agent"] += 1
        await navigate(b)

    monkeypatch.setattr(agent, "run_agent", fake_agent)
    monkeypatch.setattr("job_hunter.browser.extract._models", lambda: (FakeListing(), FakeDetail()))
    org = {"name": "Acme"}

    r1 = await agent.navigate_org(org, "industry", ["Data"])
    assert calls["agent"] == 1 and r1.stats["mode"] == "agent"
    assert [p.role for p in r1.postings] == ["Data Engineer II", "Data Analyst"]
    assert r1.update["recipe_json"] and r1.update["count"] == 4

    db.apply_org_update(r1.update)             # what persist_node does on a real run
    r2 = await agent.navigate_org(org, "industry", ["Data"])
    assert calls["agent"] == 1 and r2.stats["mode"] == "recipe"  # no agent the second time

    # 3 empty runs after having had postings forces an agent run
    for _ in range(3):
        db.apply_org_update({"name": "Acme", "sector": "industry", "status": "active", "count": 0})
    await agent.navigate_org(org, "industry", ["Data"])
    assert calls["agent"] == 2


async def test_navigate_org_failure_sets_pending_and_budget_skips(monkeypatch):
    async def broken(b, org, sector, categories):
        raise RuntimeError("agent stopped without reaching a results page")

    monkeypatch.setattr(agent, "run_agent", broken)
    r = await agent.navigate_org({"name": "Acme"}, "industry", ["Data"])
    assert r.update["status"] == "pending" and "error" in r.stats and not r.postings

    budget.start_run(max_cost_usd=0.0)
    r = await agent.navigate_org({"name": "Acme"}, "industry", ["Data"])
    assert r.stats["skipped"] == "budget" and r.update is None


def test_agent_builds_with_server_search_tool(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key")
    a = agent._build_agent(OrgBrowser(None), {"name": "Acme"}, "academic", ["Data"])
    assert a is not None
