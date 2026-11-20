"""Scoring: keyword match against config/skills.yaml + Haiku fit score, weighted by role family.

final = (keyword_weight * keyword + llm_weight * llm) * role_family_weight
"""

import asyncio
import logging
from functools import lru_cache
from typing import Literal

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from job_hunter import config, resume
from job_hunter.budget import UsageCallback
from job_hunter.models import JobPosting, ScoredPosting
from job_hunter.verify import _phrase_re

log = logging.getLogger(__name__)
SCORE_CONCURRENCY = 5


class FitResult(BaseModel):
    fit_score: int = Field(ge=0, le=100, description="How well the resume fits this posting")
    matched_skills: list[str] = Field(default_factory=list)
    missing_skills: list[str] = Field(default_factory=list)
    visa_sponsorship: Literal["yes", "no", "unknown"] = "unknown"
    reason: str = Field(description="One or two sentences")


def keyword_score(description: str) -> float:
    """0-100: weighted share of skills.yaml skills found in the description, scaled so that
    naming `keyword_saturation` of the list already scores 100."""
    skills = config.skills()["skills"]
    total = sum(skills.values())
    found = sum(w for s, w in skills.items() if _phrase_re(s).search(description or ""))
    sat = config.settings()["scoring"]["keyword_saturation"]
    return round(min(found / total / sat, 1.0) * 100, 1) if total else 0.0


def matched_skills(description: str) -> list[str]:
    return [s for s in config.skills()["skills"] if _phrase_re(s).search(description or "")]


SYSTEM = """You compare a candidate's resume with a job posting and rate the fit.

fit_score: 0-100. Judge overall relevance of the candidate's experience to the role; do not \
require every listed keyword. 80+ = strong match, 50-79 = plausible, below 50 = weak.
matched_skills / missing_skills: short skill names (max 8 each).
visa_sponsorship: "yes" or "no" only if the posting says so explicitly, otherwise "unknown".
reason: one or two sentences.

<resume>
{resume}
</resume>"""


@lru_cache
def _model():
    return ChatAnthropic(
        model=config.settings()["models"]["scorer"], max_tokens=600, temperature=0,
        callbacks=[UsageCallback()],
    ).with_structured_output(FitResult)


def _posting_prompt(p: JobPosting) -> str:
    limit = config.settings()["agent_limits"]["description_chars"]
    return (f"Company: {p.company}\nTitle: {p.role}\nLocation: {p.location}\n"
            f"Department: {p.department or ''}\n\n{(p.description or '')[:limit]}")


async def llm_score(posting: JobPosting, model=None) -> FitResult:
    """Haiku structured output. The sector's resume sits in a cached system block, so repeat
    calls within a run read it from the prompt cache."""
    system = SystemMessage(content=[{
        "type": "text", "text": SYSTEM.format(resume=resume.resume_text(posting.sector)),
        "cache_control": {"type": "ephemeral"},
    }])
    return await (model or _model()).ainvoke([system, HumanMessage(_posting_prompt(posting))])


async def score_postings(postings: list[JobPosting], model=None) -> list[ScoredPosting]:
    cfg = config.settings()
    w = cfg["scoring"]
    sem = asyncio.Semaphore(SCORE_CONCURRENCY)

    async def one(p: JobPosting) -> ScoredPosting:
        kw = keyword_score(p.description or "")
        try:
            async with sem:
                fit = await llm_score(p, model)
        except Exception as e:  # noqa: BLE001 - one bad call must not sink the run
            log.warning("scoring failed for %s: %s", p.link, e)
            fit = FitResult(fit_score=0, reason=f"llm scoring failed: {type(e).__name__}")
        weight = cfg["role_families"][p.role_family]["weight"] if p.role_family else 1.0
        final = (w["keyword_weight"] * kw + w["llm_weight"] * fit.fit_score) * weight
        return ScoredPosting(
            posting=p, keyword_score=kw, llm_score=fit.fit_score, final_score=round(final, 1),
            reason=fit.reason, matched_skills=fit.matched_skills or matched_skills(p.description),
            missing_skills=fit.missing_skills,
            # verify's phrase match beats the model's reading of free text
            visa_sponsorship="no" if p.visa_sponsorship == "no" else fit.visa_sponsorship,
        )

    scored = await asyncio.gather(*[one(p) for p in postings])
    return sorted((s for s in scored if s.final_score >= cfg["min_score"]),
                  key=lambda s: s.final_score, reverse=True)
