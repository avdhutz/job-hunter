"""VERIFY node: deterministic checks (no LLM) before anything is written to the DB.

Checks (in order): required fields, duplicate (vs DB), title -> role_family, seniority,
location, recent (posted/closing date), link alive (httpx, then headless render), still open,
title present on page (skipped for ATS postings). Thresholds and word lists live in
config/settings.yaml.
"""

import asyncio
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Literal
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import httpx
from rapidfuzz import fuzz

from job_hunter import config
from job_hunter.models import JobPosting, Rejection, RejectReason

ATS_SOURCES = {"greenhouse", "lever", "ashby"}
MIN_VISIBLE_CHARS = 200  # less visible text than this = JS shell, needs a real render
BOT_WALL_MARKERS = ("verify you are human", "just a moment", "captcha", "access denied",
                    "enable javascript and cookies")
ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4, "v": 5, "vi": 6, "vii": 7, "viii": 8, "ix": 9, "x": 10}


# --- text helpers -------------------------------------------------------------------------

def _phrase_re(phrase: str, flags: int = re.IGNORECASE) -> re.Pattern:
    """Whole-word / whole-phrase match, so "ml" never matches "html"."""
    return re.compile(rf"(?<![a-z0-9]){re.escape(phrase)}(?![a-z0-9])", flags)


def _has_phrase(text: str, phrases: list[str]) -> bool:
    return any(_phrase_re(p).search(text) for p in phrases)


def _visible_text(html: str) -> str:
    html = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()


# --- link normalization -------------------------------------------------------------------

def normalize_link(url: str) -> str:
    """Lowercase scheme/host, drop fragment, tracking params and the trailing slash."""
    cfg = config.settings()["link_normalization"]["strip_params"]
    parts = urlsplit(url.strip())
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k.lower() not in cfg and not k.lower().startswith("utm_")]
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"),
                       urlencode(sorted(query)), ""))


# --- title: role family + seniority -------------------------------------------------------

def classify_role_family(title: str, extra: str = "") -> str | None:
    """Highest-priority family whose keyword is in the title. A generic title ("Software
    Engineer") only qualifies if a family keyword also appears in `extra` (department and the
    start of the description)."""
    cfg = config.settings()
    if _has_phrase(title, cfg.get("title_exclude", [])):
        return None
    families = cfg["role_families"]  # dict order = priority order
    for name, fam in families.items():
        if _has_phrase(title, fam["keywords"]):
            return name
    if extra and _has_phrase(title, cfg["generic_titles"]):
        for name, fam in families.items():
            if _has_phrase(extra, fam["keywords"]):
                return name
    return None


def title_could_match(title: str) -> bool:
    """Cheap pre-check before opening a posting page: a family keyword, or a generic title that
    verify will judge again once it has the department and description."""
    cfg = config.settings()
    if _has_phrase(title, cfg.get("title_exclude", [])):
        return False
    return bool(classify_role_family(title)) or _has_phrase(title, cfg["generic_titles"])


def family_rank(title: str) -> int:
    """Position of the title's family in priority order (generic titles sort last)."""
    families = list(config.settings()["role_families"])
    family = classify_role_family(title)
    return families.index(family) if family else len(families)


def passes_seniority(title: str, sector: str) -> bool:
    cfg = config.settings()["seniority"]
    excluded = cfg["academic_exclude"] if sector == "academic" else cfg["exclude"]
    if _has_phrase(title, excluded):
        return False
    m = re.search(r"(?:^|[\s,(-])(\d+|[ivx]+)\)?$", title.strip().lower())
    if m:
        level = int(m.group(1)) if m.group(1).isdigit() else ROMAN.get(m.group(1))
        if level is not None and level > cfg["max_level"]:
            return False
    return True


# --- location -----------------------------------------------------------------------------

def is_us_location(location: str) -> bool:
    """True if any part of a multi-location string is in the US. Bare "Remote" counts."""
    cfg = config.settings()["locations"]
    states = cfg["us_states"]
    for part in re.split(r"[;|\n]| or | and ", location):
        if not part.strip():
            continue
        if any(_phrase_re(n).search(part) for n in states):
            return True
        if any(_phrase_re(c, 0).search(part) for c in states.values()):  # codes: uppercase only
            return True
        for term in cfg["country_terms"]:
            short = len(term) <= 4  # "US"/"USA" must be uppercase so "us" the word never matches
            if _phrase_re(term.upper() if short else term, 0 if short else re.IGNORECASE).search(part):
                return True
        leftover = re.sub(r"(?i)remote|hybrid|on-?site|anywhere|[^a-z]+", " ", part).strip()
        if not leftover:  # bare "Remote" / "Hybrid"
            return True
    return False


# --- dates --------------------------------------------------------------------------------

def parse_relative_date(text: str, today: date) -> date | None:
    """"Posted 12 Days Ago", "Yesterday", "30+ days ago". "N+ days" resolves to N+1 days back,
    so it is older than an N-day window."""
    t = text.lower()
    if re.search(r"\btoday\b|\bjust posted\b|\bposted now\b", t):
        return today
    if re.search(r"\byesterday\b", t):
        return today - timedelta(days=1)
    m = re.search(r"(\d+|an?)(\+)?\s*(day|week|month|year)s?\b", t)
    if not m:
        return None
    n = 1 if m.group(1) in ("a", "an") else int(m.group(1))
    days = n * {"day": 1, "week": 7, "month": 30, "year": 365}[m.group(3)]
    return today - timedelta(days=days + (1 if m.group(2) else 0))


def date_check(p: JobPosting, today: date) -> tuple[date | None, bool]:
    """Return (posted_date, ok). Undated postings are accepted; academic postings can pass on
    a future closing date instead of a posting date."""
    max_age = config.settings()["verify"]["max_age_days"]
    posted = p.posted_date or (parse_relative_date(p.posted_text, today) if p.posted_text else None)
    if posted:
        if (today - posted).days <= max_age:
            return posted, True
        return posted, bool(p.closing_date and p.closing_date >= today)
    if p.closing_date and p.closing_date < today:
        return None, False
    return None, True


# --- link check ---------------------------------------------------------------------------

@dataclass
class LinkResult:
    status: Literal["ok", "dead", "closed", "blocked"]
    final_url: str = ""
    text: str = ""


Renderer = Callable[[str], Awaitable[LinkResult]]


def _generic_redirect(url: str, final_url: str) -> bool:
    """Redirected up to an ancestor path (e.g. /careers/job/123 -> /careers)."""
    a, b = urlsplit(url), urlsplit(final_url)
    return a.netloc == b.netloc and a.path.rstrip("/") != b.path.rstrip("/") \
        and a.path.startswith(b.path.rstrip("/") + "/")


def classify_response(url: str, status: int, final_url: str, text: str) -> LinkResult | None:
    """None means inconclusive (blocked, bot wall or JS shell): escalate to the next tier."""
    if status in (404, 410):
        return LinkResult("dead", final_url)
    if status >= 400:
        return None
    if _generic_redirect(url, final_url):
        return LinkResult("closed", final_url, text)
    low = text.lower()
    if any(m in low for m in config.settings()["verify"]["closed_markers"]):
        return LinkResult("closed", final_url, text)
    if any(m in low for m in BOT_WALL_MARKERS) or len(text) < MIN_VISIBLE_CHARS:
        return None
    return LinkResult("ok", final_url, text)


async def tier1(client: httpx.AsyncClient, url: str) -> LinkResult | None:
    try:
        r = await client.get(url, follow_redirects=True)
    except httpx.HTTPError:
        return None
    return classify_response(url, r.status_code, str(r.url), _visible_text(r.text))


class PlaywrightRenderer:
    """Tier 2: one shared headless browser, started on first use."""

    def __init__(self, timeout_s: float = 20, concurrency: int = 3):
        self.timeout_ms = int(timeout_s * 1000)
        self._sem = asyncio.Semaphore(concurrency)
        self._lock = asyncio.Lock()
        self._pw = self._browser = None

    async def _get_browser(self):
        async with self._lock:
            if self._browser is None:
                from playwright.async_api import async_playwright
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(headless=True)
            return self._browser

    async def __call__(self, url: str) -> LinkResult:
        async with self._sem:
            try:
                ctx = await (await self._get_browser()).new_context()
                try:
                    page = await ctx.new_page()
                    resp = await page.goto(url, wait_until="domcontentloaded",
                                           timeout=self.timeout_ms)
                    try:
                        await page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
                    text = re.sub(r"\s+", " ", await page.inner_text("body")).strip()
                    status = resp.status if resp else 200
                    return classify_response(url, status, page.url, text) \
                        or LinkResult("blocked", page.url, text)
                finally:
                    await ctx.close()
            except Exception:
                return LinkResult("blocked", url)

    async def close(self) -> None:
        if self._browser:
            await self._browser.close()
            await self._pw.stop()


async def check_link(client: httpx.AsyncClient, url: str, render: Renderer) -> LinkResult:
    return await tier1(client, url) or await render(url)


# --- node entry point ---------------------------------------------------------------------

async def verify_postings(
    postings: list[JobPosting],
    known_links: set[str],
    *,
    client: httpx.AsyncClient | None = None,
    renderer: Renderer | None = None,
    today: date | None = None,
) -> tuple[list[JobPosting], list[Rejection]]:
    """Return (verified, rejected). Duplicates are dropped silently."""
    cfg = config.settings()["verify"]
    today = today or date.today()
    rejected: list[Rejection] = []
    candidates: list[JobPosting] = []
    seen = set(known_links)

    def reject(p: JobPosting, reason: RejectReason) -> None:
        rejected.append(Rejection(posting=p, reason=reason))

    for p in postings:
        if not (p.company and p.role and p.link and p.location):
            reject(p, "missing_fields")
            continue
        link = normalize_link(p.link)
        if link in seen:
            continue
        seen.add(link)
        extra = f"{p.department or ''} {(p.description or '')[:1000]}"
        family = classify_role_family(p.role, extra)
        if not family:
            reject(p, "title_mismatch")
        elif not passes_seniority(p.role, p.sector):
            reject(p, "seniority")
        elif not is_us_location(p.location):
            reject(p, "location")
        else:
            posted, ok = date_check(p, today)
            if not ok:
                reject(p, "stale")
            else:
                candidates.append(p.model_copy(update={
                    "link": link, "role_family": family, "posted_date": posted}))

    own_client = client is None
    client = client or httpx.AsyncClient(
        timeout=cfg["http_timeout_s"], headers={"User-Agent": "Mozilla/5.0 (job-hunter)"})
    own_renderer = renderer is None
    render = renderer or PlaywrightRenderer()
    sem = asyncio.Semaphore(cfg["concurrency"])

    async def check(p: JobPosting) -> tuple[JobPosting, RejectReason | None]:
        async with sem:
            res = await check_link(client, p.link, render)
        if res.status == "dead":
            return p, "dead_link"
        if res.status == "closed":
            return p, "closed"
        update: dict = {"link_verified": res.status == "ok"}
        if res.status == "ok":
            if p.source not in ATS_SOURCES and fuzz.partial_ratio(
                    p.role.lower(), res.text.lower()) < cfg["title_match_threshold"]:
                return p, "title_not_on_page"
        if any(ph in f"{p.description or ''} {res.text}".lower() for ph in cfg["no_visa_phrases"]):
            update["visa_sponsorship"] = "no"
        return p.model_copy(update=update), None

    try:
        results = await asyncio.gather(*[check(p) for p in candidates])
    finally:
        if own_client:
            await client.aclose()
        if own_renderer:
            await render.close()

    verified = []
    for p, reason in results:
        if reason:
            reject(p, reason)
        else:
            verified.append(p)
    return verified, rejected
