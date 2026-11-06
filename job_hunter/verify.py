"""VERIFY node: deterministic checks before anything is written to the DB.

Checks (in order): required fields, duplicate (vs DB), relevant title -> role_family,
location, recent (posted/closing date), link alive, still open, title present on page.
"""

from job_hunter.models import JobPosting, Rejection


def classify_role_family(title: str) -> str | None:
    """Return the highest-priority role family whose keywords match the title."""
    raise NotImplementedError


async def verify_postings(
    postings: list[JobPosting], known_links: set[str]
) -> tuple[list[JobPosting], list[Rejection]]:
    """Return (verified, rejected). Duplicates are dropped silently."""
    # TODO: implement checks; link checks via httpx.AsyncClient with a semaphore
    return [], []
