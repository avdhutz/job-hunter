"""Main pipeline graph.

START -> [industry, academic] (parallel) -> verify -> score -> persist -> END
"""

from langgraph.graph import END, START, StateGraph

from job_hunter import db
from job_hunter.academic.graph import academic_node
from job_hunter.industry.graph import industry_node
from job_hunter.score import score_postings
from job_hunter.state import State
from job_hunter.verify import verify_postings


async def verify_node(state: State) -> dict:
    verified, rejected = await verify_postings(state.get("candidates", []), db.known_links())
    return {"verified": verified, "rejected": rejected}


async def score_node(state: State) -> dict:
    return {"scored": await score_postings(state.get("verified", []))}


def persist_node(state: State) -> dict:
    if not state.get("dry_run"):
        db.insert_jobs(state.get("scored", []), state["run_id"])
        db.insert_rejected(state.get("rejected", []), state["run_id"])
    return {}


def build_graph():
    g = StateGraph(State)
    g.add_node("industry", industry_node)
    g.add_node("academic", academic_node)
    g.add_node("verify", verify_node)
    g.add_node("score", score_node)
    g.add_node("persist", persist_node)

    g.add_edge(START, "industry")
    g.add_edge(START, "academic")
    g.add_edge(["industry", "academic"], "verify")
    g.add_edge("verify", "score")
    g.add_edge("score", "persist")
    g.add_edge("persist", END)
    return g.compile()


graph = build_graph()  # referenced by langgraph.json for `langgraph dev` (Studio)
