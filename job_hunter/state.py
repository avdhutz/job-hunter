"""LangGraph state for one pipeline run."""

import operator
from typing import Annotated, TypedDict

from job_hunter.models import JobPosting, Rejection, ScoredPosting, Sector


class State(TypedDict, total=False):
    run_id: str
    sectors: list[Sector]  # which subgraphs to run (CLI --only)
    dry_run: bool
    # Both subgraphs append here in parallel; the reducer merges their lists.
    candidates: Annotated[list[JobPosting], operator.add]
    verified: list[JobPosting]
    rejected: list[Rejection]
    scored: list[ScoredPosting]
    # Per-org counts (fetched / filtered out / kept / error) for the end-of-run summary.
    org_stats: Annotated[list[dict], operator.add]
    # Recipe / status changes per org, written by persist_node (skipped on dry runs).
    org_updates: Annotated[list[dict], operator.add]
