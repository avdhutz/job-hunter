"""Shortcut for industry orgs on public ATS JSON APIs (no LLM, exact dates).

Greenhouse: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
Lever:      https://api.lever.co/v0/postings/{slug}?mode=json
Ashby:      https://api.ashbyhq.com/posting-api/job-board/{slug}
"""

from job_hunter.models import JobPosting


async def fetch_ats(company: dict) -> list[JobPosting]:
    # TODO
    return []
