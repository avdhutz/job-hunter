"""Scoring: keyword match against config/skills.yaml + Haiku fit score, weighted by role family.

final = (keyword_weight * keyword + llm_weight * llm) * role_family_weight
"""

from job_hunter.models import JobPosting, ScoredPosting


def keyword_score(description: str) -> float:
    """0-100: weighted share of skills.yaml skills found in the job description."""
    raise NotImplementedError


async def llm_score(posting: JobPosting) -> dict:
    """Haiku structured output: fit_score, matched/missing skills, visa_sponsorship, reason.
    The sector's resume goes in a cached system prompt."""
    raise NotImplementedError


async def score_postings(postings: list[JobPosting]) -> list[ScoredPosting]:
    # TODO
    return []
