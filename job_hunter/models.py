"""Shared data models. Both sectors produce JobPosting, so everything downstream is shared."""

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

Sector = Literal["industry", "academic"]
RejectReason = Literal[
    "missing_fields", "dead_link", "closed", "title_mismatch", "seniority", "stale", "location",
    "title_not_on_page",
]


class JobPosting(BaseModel):
    company: str
    role: str
    link: str
    sector: Sector
    source: str = Field(description="greenhouse | lever | ashby | browser_agent | recipe")
    posted_date: date | None = None
    posted_text: str | None = None  # raw page text such as "Posted 12 Days Ago"; verify resolves it
    closing_date: date | None = None  # academic: closing / review-begins date
    location: str | None = None
    department: str | None = None  # academic
    position_type: Literal["staff"] | None = None  # academic: staff roles only
    description: str | None = None
    role_family: str | None = None  # set by verify (title keyword match)
    visa_sponsorship: Literal["yes", "no", "unknown"] = "unknown"  # verify sets "no" from phrases
    link_verified: bool = False  # set by verify: True when the link check succeeded


class Rejection(BaseModel):
    posting: JobPosting
    reason: RejectReason


class ScoredPosting(BaseModel):
    posting: JobPosting
    keyword_score: float
    llm_score: float
    final_score: float
    reason: str = ""
    matched_skills: list[str] = []
    missing_skills: list[str] = []
    visa_sponsorship: Literal["yes", "no", "unknown"] = "unknown"
