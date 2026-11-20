"""Shortcut for industry orgs on public ATS JSON APIs (no LLM, exact dates).

Greenhouse: https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true
Lever:      https://api.lever.co/v0/postings/{slug}?mode=json
Ashby:      https://api.ashbyhq.com/posting-api/job-board/{slug}

Postings are filtered by title (role family), seniority and location here, before verify's
link checks, so big boards don't cost link checks or flood `rejected`. Non-matching titles are
only counted, never stored.
"""

import html
import logging
from datetime import UTC, date, datetime

import httpx

from job_hunter import config
from job_hunter.models import JobPosting
from job_hunter.verify import (
    _visible_text,
    classify_role_family,
    date_check,
    is_us_location,
    passes_seniority,
)

log = logging.getLogger(__name__)

URLS = {
    "greenhouse": "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true",
    "lever": "https://api.lever.co/v0/postings/{slug}?mode=json",
    "ashby": "https://api.ashbyhq.com/posting-api/job-board/{slug}",
}


def _date(value: str | int | None) -> date | None:
    """ISO string or epoch milliseconds (Lever) -> date."""
    if not value:
        return None
    if isinstance(value, int):
        return datetime.fromtimestamp(value / 1000, tz=UTC).date()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except ValueError:
        return None


def _parse_greenhouse(data: dict, company: str) -> list[JobPosting]:
    return [
        JobPosting(
            company=company, role=j["title"], link=j["absolute_url"], sector="industry",
            source="greenhouse", posted_date=_date(j.get("first_published") or j.get("updated_at")),
            location=(j.get("location") or {}).get("name"),
            department=", ".join(d["name"] for d in j.get("departments") or []) or None,
            description=_visible_text(html.unescape(j.get("content") or "")) or None,
        )
        for j in data.get("jobs", [])
    ]


def _parse_lever(data: list, company: str) -> list[JobPosting]:
    out = []
    for j in data:
        cats = j.get("categories") or {}
        locations = cats.get("allLocations") or [cats.get("location")]
        out.append(JobPosting(
            company=company, role=j["text"], link=j["hostedUrl"], sector="industry",
            source="lever", posted_date=_date(j.get("createdAt")),
            location="; ".join(loc for loc in locations if loc) or None,
            department=cats.get("department") or cats.get("team"),
            description=j.get("descriptionPlain"),
        ))
    return out


def _parse_ashby(data: dict, company: str) -> list[JobPosting]:
    out = []
    for j in data.get("jobs", []):
        if j.get("isListed") is False:
            continue
        locations = [j.get("location")] + [
            s.get("location") for s in j.get("secondaryLocations") or []]
        out.append(JobPosting(
            company=company, role=j["title"], link=j["jobUrl"], sector="industry",
            source="ashby", posted_date=_date(j.get("publishedAt")),
            location="; ".join(loc for loc in locations if loc) or None,
            department=j.get("department") or j.get("team"),
            description=j.get("descriptionPlain"),
        ))
    return out


PARSERS = {"greenhouse": _parse_greenhouse, "lever": _parse_lever, "ashby": _parse_ashby}


def filter_postings(postings: list[JobPosting], limit: int) -> tuple[list[JobPosting], dict]:
    """Keep role-family + seniority + US-location + fresh-enough matches; cap at `limit`, best family first.
    Returns (kept, counts). A missing location passes through so verify rejects it explicitly."""
    families = list(config.settings()["role_families"])  # priority order
    counts = {"fetched": len(postings), "title_mismatch": 0, "seniority": 0, "location": 0,
              "stale": 0}
    today = date.today()
    kept = []
    for p in postings:
        extra = f"{p.department or ''} {(p.description or '')[:1000]}"
        family = classify_role_family(p.role, extra)
        if not family:
            counts["title_mismatch"] += 1
        elif not passes_seniority(p.role, p.sector):
            counts["seniority"] += 1
        elif p.location and not is_us_location(p.location):
            counts["location"] += 1
        elif not date_check(p, today)[1]:  # before the cap, or old postings use it up
            counts["stale"] += 1
        else:
            kept.append((families.index(family), p))
    kept.sort(key=lambda t: t[0])  # stable: keeps board order within a family
    counts["kept"] = min(len(kept), limit)
    counts["over_limit"] = max(len(kept) - limit, 0)
    return [p for _, p in kept[:limit]], counts


async def fetch_ats(
    company: dict, client: httpx.AsyncClient | None = None
) -> tuple[list[JobPosting], dict]:
    """Return (filtered postings, per-org counts). Never raises: a failing org yields
    ([], {"org": ..., "error": ...}) so the rest of the run continues."""
    name, kind = company["name"], company["ats"]
    own_client = client is None
    client = client or httpx.AsyncClient(timeout=config.settings()["verify"]["http_timeout_s"])
    try:
        resp = await client.get(URLS[kind].format(slug=company["slug"]), follow_redirects=True)
        resp.raise_for_status()
        postings = PARSERS[kind](resp.json(), name)
    except (httpx.HTTPError, ValueError, KeyError) as e:
        log.warning("ATS fetch failed for %s: %s", name, e)
        return [], {"org": name, "error": f"{type(e).__name__}: {e}"}
    finally:
        if own_client:
            await client.aclose()
    kept, counts = filter_postings(postings, config.settings()["agent_limits"]["max_postings_per_org"])
    return kept, {"org": name, **counts}
