from pathlib import Path

import httpx

from job_hunter.industry.ats import fetch_ats

FIX = Path(__file__).parent / "fixtures"


def client_for(fixture: str, status: int = 200) -> httpx.AsyncClient:
    body = (FIX / fixture).read_text()
    return httpx.AsyncClient(transport=httpx.MockTransport(
        lambda req: httpx.Response(status, content=body if status == 200 else "")))


async def test_greenhouse_filters_and_orders_by_family():
    org = {"name": "Acme", "ats": "greenhouse", "slug": "acme"}
    postings, counts = await fetch_ats(org, client_for("greenhouse.json"))
    # FDE first, then ML, then data analyst (family priority), not board order
    assert [p.role for p in postings] == ["Forward Deployed Engineer", "ML Engineer", "Data Analyst II"]
    assert counts == {"org": "Acme", "fetched": 6, "title_mismatch": 1, "seniority": 1,
                      "location": 1, "stale": 0, "kept": 3, "over_limit": 0}
    fde = postings[0]
    assert fde.source == "greenhouse" and fde.department == "Engineering"
    assert fde.posted_date.isoformat() == "2026-10-30"
    assert fde.description == "Work with customers on LLM rollouts."


async def test_lever_multi_location_and_epoch_date():
    org = {"name": "Acme", "ats": "lever", "slug": "acme"}
    postings, counts = await fetch_ats(org, client_for("lever.json"))
    assert [p.role for p in postings] == ["AI Engineer I"]
    assert postings[0].location == "Palo Alto, CA; London"
    assert postings[0].posted_date is not None
    assert counts["title_mismatch"] == 1


async def test_ashby_skips_unlisted():
    org = {"name": "Acme", "ats": "ashby", "slug": "acme"}
    postings, counts = await fetch_ats(org, client_for("ashby.json"))
    assert [p.role for p in postings] == ["Research Data Scientist"]
    assert counts["fetched"] == 1


async def test_failure_is_isolated():
    org = {"name": "Acme", "ats": "greenhouse", "slug": "acme"}
    postings, counts = await fetch_ats(org, client_for("greenhouse.json", status=404))
    assert postings == [] and "error" in counts


async def test_max_postings_cap(monkeypatch):
    from job_hunter import config
    s = dict(config.settings(), agent_limits={**config.settings()["agent_limits"], "max_postings_per_org": 2})
    monkeypatch.setattr(config, "settings", lambda: s)
    org = {"name": "Acme", "ats": "greenhouse", "slug": "acme"}
    postings, counts = await fetch_ats(org, client_for("greenhouse.json"))
    assert len(postings) == 2 and counts["over_limit"] == 1
    assert postings[1].role == "ML Engineer"


async def test_stale_postings_are_dropped_before_the_cap(monkeypatch):
    from datetime import date, timedelta

    from job_hunter.industry import ats
    old = (date.today() - timedelta(days=400)).isoformat() + "T00:00:00Z"
    new = date.today().isoformat() + "T00:00:00Z"
    jobs = [{"title": "Data Engineer", "absolute_url": f"https://x/{i}", "first_published": old if i < 3 else new,
             "location": {"name": "Remote"}, "departments": [], "content": ""} for i in range(5)]
    postings = ats._parse_greenhouse({"jobs": jobs}, "Acme")
    kept, counts = ats.filter_postings(postings, limit=2)
    assert counts["stale"] == 3 and [p.link for p in kept] == ["https://x/3", "https://x/4"]
