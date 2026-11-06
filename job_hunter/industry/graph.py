"""Industry subgraph: "<company> careers" -> careers site -> category filter -> postings.
Orgs with a known ATS (greenhouse/lever/ashby) use the JSON API instead of the browser."""

import asyncio

from job_hunter import config
from job_hunter.browser.agent import navigate_org
from job_hunter.industry.ats import fetch_ats
from job_hunter.state import State


async def _one(company: dict, categories: list[str]):
    if company.get("ats") in {"greenhouse", "lever", "ashby"}:
        return await fetch_ats(company)
    return await navigate_org(company, "industry", categories)


async def industry_node(state: State) -> dict:
    if "industry" not in state.get("sectors", ["industry", "academic"]):
        return {"candidates": []}
    cfg = config.companies()
    results = await asyncio.gather(*[_one(c, cfg["category_filters"]) for c in cfg["companies"]])
    return {"candidates": [p for r in results for p in r]}
