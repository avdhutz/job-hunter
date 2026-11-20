"""Industry subgraph: "<company> careers" -> careers site -> category filter -> postings.
Orgs with a known ATS (greenhouse/lever/ashby) use the JSON API instead of the browser."""

import asyncio

from job_hunter import config, db
from job_hunter.browser.agent import OrgResult, navigate_org
from job_hunter.industry.ats import fetch_ats
from job_hunter.state import State


async def _one(company: dict, categories: list[str], known: set[str]) -> OrgResult:
    if company.get("ats") in {"greenhouse", "lever", "ashby"}:
        postings, stats = await fetch_ats(company)
        return OrgResult(postings, stats)
    return await navigate_org(company, "industry", categories, known)


async def industry_node(state: State) -> dict:
    if "industry" not in state.get("sectors", ["industry", "academic"]):
        return {"candidates": []}
    cfg = config.companies()
    known = db.known_links()
    results = await asyncio.gather(*[_one(c, cfg["category_filters"], known) for c in cfg["companies"]])
    return {
        "candidates": [p for r in results for p in r.postings],
        "org_stats": [r.stats for r in results],
        "org_updates": [r.update for r in results if r.update],
    }
