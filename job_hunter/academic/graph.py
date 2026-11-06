"""Academic subgraph: "<university> careers" -> careers site -> STAFF positions
-> job category filter (Engineering, Data Analytics, IT, ...) -> postings."""

import asyncio

from job_hunter import config
from job_hunter.browser.agent import navigate_org
from job_hunter.state import State


async def academic_node(state: State) -> dict:
    if "academic" not in state.get("sectors", ["industry", "academic"]):
        return {"candidates": []}
    cfg = config.academic()
    results = await asyncio.gather(
        *[navigate_org(u, "academic", cfg["category_filters"]) for u in cfg["universities"]]
    )
    return {"candidates": [p for r in results for p in r]}
