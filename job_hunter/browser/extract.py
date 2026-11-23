"""Turns pages into JobPostings with a cheap model (Haiku). Navigation is the agent's job;
this stage only reads. URLs come from the DOM (the href of a numbered link, or the page URL
after opening it), never from model output."""

import logging
from datetime import date
from functools import lru_cache

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from job_hunter import config
from job_hunter.browser.tools import OrgBrowser
from job_hunter.budget import UsageCallback
from job_hunter.models import JobPosting, Sector
from job_hunter.verify import family_rank, normalize_link, passes_seniority, title_could_match

log = logging.getLogger(__name__)


class ListingItem(BaseModel):
    ref: int = Field(description="Number of the link to this job posting in ELEMENTS")
    title: str
    location: str | None = None
    posted_text: str | None = Field(None, description='e.g. "Posted 12 Days Ago", or a date')


class Listing(BaseModel):
    items: list[ListingItem] = Field(default_factory=list)
    next_ref: int | None = Field(
        None, description="Number of the 'next page' / 'load more' control, if more results exist")


class Detail(BaseModel):
    role: str | None = Field(None, description="Job title as written on the page")
    location: str | None = None
    department: str | None = None
    posted_text: str | None = Field(None, description="Posting date or relative age, as written")
    closing_date: str | None = Field(None, description="YYYY-MM-DD if a closing date is shown")


LISTING_SYSTEM = """You read a careers-site search-results page. List every individual job \
posting shown (not filters, menus or footer links). Use the ref number of the link that opens \
the posting. Copy title, location and posting date/age as shown; leave a field null if not \
shown. If the page has a next-page or load-more control, give its ref as next_ref."""

DETAIL_SYSTEM = """You read a single job posting page. Extract the job title, location(s), \
department/team, the posting date or age as written, and the closing date if shown. Use null \
for anything not on the page. Do not guess."""


@lru_cache
def _models():
    m = config.settings()["models"]["extractor"]
    kw = {"model": m, "callbacks": [UsageCallback()]}
    return (ChatAnthropic(max_tokens=3000, **kw).with_structured_output(Listing),
            ChatAnthropic(max_tokens=400, **kw).with_structured_output(Detail))


def _closing(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


async def list_postings(
    b: OrgBrowser, known: set[str], sector: Sector, model=None
) -> tuple[list[ListingItem], list[str], dict]:
    """Read the results page(s). Returns (items, hrefs aligned with items, counts). Titles and
    seniority are filtered here, before any posting page is opened."""
    limits = config.settings()["agent_limits"]
    model = model or _models()[0]
    counts = {"listed": 0, "already_known": 0, "title_mismatch": 0, "seniority": 0}
    seen: set[str] = set()
    kept: list[tuple[ListingItem, str]] = []
    for _ in range(limits["max_listing_pages"]):
        snap = await b.snapshot(max_chars=limits["page_text_chars"], max_elements=250)
        listing: Listing = await model.ainvoke(
            [SystemMessage(LISTING_SYSTEM), HumanMessage(snap)])
        for item in listing.items:
            href = b.href(item.ref)
            if not href or href in seen:
                continue
            seen.add(href)
            counts["listed"] += 1
            if normalize_link(href) in known:
                counts["already_known"] += 1
            elif not title_could_match(item.title):
                counts["title_mismatch"] += 1
            elif not passes_seniority(item.title, sector):
                counts["seniority"] += 1
            else:
                kept.append((item, href))
        if not listing.next_ref or listing.next_ref not in b.elements:
            break
        await b.click(listing.next_ref)
    kept.sort(key=lambda t: family_rank(t[0].title))
    kept = kept[: limits["max_postings_per_org"]]
    counts["kept"] = len(kept)
    return [i for i, _ in kept], [h for _, h in kept], counts


async def read_posting(
    b: OrgBrowser, href: str, listed: ListingItem, org: dict, sector: Sector, model=None
) -> JobPosting | None:
    """Open one posting and extract its fields. The link is the URL actually reached."""
    limits = config.settings()["agent_limits"]
    model = model or _models()[1]
    try:
        await b.goto(href)
        text = await b.page_text()
        detail: Detail = await model.ainvoke([
            SystemMessage(DETAIL_SYSTEM), HumanMessage(text[: limits["page_text_chars"]])])
    except Exception as e:  # noqa: BLE001 - a bad page costs one posting, not the org
        log.warning("could not read %s: %s", href, e)
        return None
    return JobPosting(
        company=org["name"], role=detail.role or listed.title, link=b.page.url, sector=sector,
        source="browser_agent", location=detail.location or listed.location,
        department=detail.department, posted_text=detail.posted_text or listed.posted_text,
        closing_date=_closing(detail.closing_date),
        position_type="staff" if sector == "academic" else None,
        description=text[: limits["description_chars"]],
    )
