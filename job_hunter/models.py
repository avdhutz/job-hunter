"""Shared data models. Both sectors produce JobPosting, so everything downstream is shared."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Sector = Literal["industry", "academic"]


class JobPosting(BaseModel):
    company: str
    role: str
    link: str
    sector: Sector
    source: str = Field(description="greenhouse | lever | ashby | browser_agent | recipe")
    posted_date: date | None = None
    closing_date: date | None = None  # academic: closing / review-begins date
    location: str | None = None
    department: str | None = None  # academic
    position_type: str | None = None  # academic: staff | postdoc | faculty
    description: str | None = None
    role_family: str | None = None  # set by verify (title keyword match)


class Rejection(BaseModel):
    posting: JobPosting
    reason: str  # missing_fields | dead_link | closed | title_mismatch | stale | no_date | off_target | location


class ScoredPosting(BaseModel):
    posting: JobPosting
    keyword_score: float
    llm_score: float
    final_score: float
    reason: str = ""
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    visa_sponsorship: Literal["yes", "no", "unknown"] = "unknown"
