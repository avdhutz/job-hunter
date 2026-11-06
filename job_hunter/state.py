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
