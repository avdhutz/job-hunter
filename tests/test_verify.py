from datetime import date

import httpx
import pytest

from job_hunter.models import JobPosting
from job_hunter.verify import (
    LinkResult,
    classify_response,
    classify_role_family,
    date_check,
    is_us_location,
    normalize_link,
    parse_relative_date,
    passes_seniority,
    verify_postings,
)

TODAY = date(2026, 11, 6)
BODY = "<html><body>" + "Senior Data Engineer builds pipelines. " * 20 + "</body></html>"
UNRELATED = "<p>" + "Completely unrelated page content. " * 20


def posting(**kw) -> JobPosting:
    base = dict(company="Acme", role="Data Engineer", link="https://acme.com/jobs/1",
                sector="industry", source="browser_agent", location="Austin, TX")
    return JobPosting(**{**base, **kw})


def test_normalize_link():
    assert normalize_link("HTTPS://Acme.com/jobs/1/?utm_source=x&gh_jid=7&ref=y#top") == \
        "https://acme.com/jobs/1?gh_jid=7"


@pytest.mark.parametrize("title,family", [
    ("Forward Deployed Engineer", "fde"),
    ("AI Engineer I", "ai"),
    ("Machine Learning Engineer", "ml"),
    ("Senior ML Engineer", "ml"),
    ("Analytics Engineer", "data_engineering"),
    ("BI Analyst", "data_analyst"),
    ("Data Scientist II", "data_science"),
    ("HTML Developer", None),          # "ml" must not match inside "html"
    ("Software Engineer", None),       # generic, no second signal
    ("Account Executive", None),
    ("Applied AI Architect, Public Sector", None),   # architects are excluded
    ("Solutions Architect - Retail", None),
    ("Sr. Solutions Engineer - AI Natives", None),   # pre-sales
    ("Sales Engineer, AI", None),
    ("Applied AI Engineer", "fde"),
])
def test_role_family(title, family):
    assert classify_role_family(title) == family


def test_generic_title_needs_second_signal():
    assert classify_role_family("Software Engineer", "Machine Learning Platform team") == "ml"
    assert classify_role_family("Software Engineer", "Payments team") is None


@pytest.mark.parametrize("title,ok", [
    ("Data Engineer", True), ("Data Engineer I", True), ("Data Engineer III", True),
    ("Senior Data Engineer", True), ("Data Engineer IV", False), ("Data Engineer 5", False),
    ("Staff Data Engineer", False), ("Lead Data Scientist", False),
    ("Principal ML Engineer", False), ("Data Science Manager", False),
    ("Data Engineer Intern", False), ("Head of Data", False),
])
def test_seniority_industry(title, ok):
    assert passes_seniority(title, "industry") is ok


def test_seniority_academic_staff_is_not_excluded():
    assert passes_seniority("Staff Data Analyst", "academic")
    assert not passes_seniority("Senior Staff Data Analyst", "academic")
    assert not passes_seniority("Staff Scientist", "academic")


@pytest.mark.parametrize("loc,ok", [
    ("Austin, TX", True), ("Remote", True), ("San Francisco, California", True),
    ("United States", True), ("Remote - USA", True), ("London, UK", False),
    ("Remote - Canada", False), ("Austin", False), ("Australia", False),
    ("Toronto; New York, NY", True), ("Bangalore, India | Remote", True),
])
def test_location(loc, ok):
    assert is_us_location(loc) is ok


def test_relative_dates():
    assert parse_relative_date("Posted 12 Days Ago", TODAY) == date(2026, 10, 25)
    assert parse_relative_date("Posted Today", TODAY) == TODAY
    assert parse_relative_date("Posted 30+ Days Ago", TODAY) == date(2026, 10, 6)
    assert parse_relative_date("no date here", TODAY) is None


def test_date_check():
    assert date_check(posting(), TODAY) == (None, True)  # undated accepted
    assert date_check(posting(posted_date=date(2026, 9, 1)), TODAY)[1] is False
    assert date_check(posting(posted_text="Posted 30+ Days Ago"), TODAY)[1] is False
    assert date_check(posting(posted_date=date(2026, 9, 1), closing_date=date(2026, 12, 1)),
                      TODAY)[1] is True
    assert date_check(posting(closing_date=date(2026, 10, 1)), TODAY)[1] is False


def test_classify_response():
    u = "https://a.com/j/1"
    assert classify_response(u, 404, u, "").status == "dead"
    assert classify_response(u, 403, u, "") is None
    assert classify_response(u, 200, u, "tiny") is None
    assert classify_response("https://a.com/careers/j/1", 200, "https://a.com/careers",
                             "x" * 300).status == "closed"
    assert classify_response(u, 200, u, "This job is no longer available. " * 20).status == "closed"
    assert classify_response(u, 200, u, "x" * 300).status == "ok"


async def no_render(url: str) -> LinkResult:
    return LinkResult("blocked", url)


async def run(postings, handler, known=frozenset(), renderer=no_render):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return await verify_postings(postings, set(known), client=client,
                                 renderer=renderer, today=TODAY)


async def test_verify_happy_path_and_rejections():
    good = posting(link="https://acme.com/jobs/1?utm_source=x")
    dup = posting(link="https://acme.com/jobs/2")
    bad_title = posting(role="Account Executive", link="https://acme.com/jobs/3")
    no_loc = posting(location=None, link="https://acme.com/jobs/4")
    verified, rejected = await run([good, dup, bad_title, no_loc],
                                   lambda req: httpx.Response(200, text=BODY),
                                   known={"https://acme.com/jobs/2"})
    assert [v.link for v in verified] == ["https://acme.com/jobs/1"]
    assert verified[0].link_verified and verified[0].role_family == "data_engineering"
    assert sorted(r.reason for r in rejected) == ["missing_fields", "title_mismatch"]


async def test_verify_dead_closed_and_title_not_on_page():
    def handler(req):
        if req.url.path.endswith("/dead"):
            return httpx.Response(404)
        if req.url.path.endswith("/closed"):
            return httpx.Response(200, text="<p>" + "This job is no longer available. " * 20)
        return httpx.Response(200, text=UNRELATED)
    ps = [posting(link=f"https://acme.com/{s}") for s in ("dead", "closed", "other")]
    verified, rejected = await run(ps, handler)
    assert not verified
    assert sorted(r.reason for r in rejected) == ["closed", "dead_link", "title_not_on_page"]


async def test_verify_ats_skips_title_check_and_blocked_is_kept_unverified():
    def handler(req):
        if req.url.path.endswith("/blocked"):
            return httpx.Response(403)
        return httpx.Response(200, text=UNRELATED)
    ats = posting(link="https://acme.com/ats", source="greenhouse")
    blocked = posting(link="https://acme.com/blocked")
    verified, rejected = await run([ats, blocked], handler)
    assert not rejected
    by_link = {v.link: v for v in verified}
    assert by_link["https://acme.com/ats"].link_verified
    assert not by_link["https://acme.com/blocked"].link_verified


async def test_verify_falls_back_to_render_and_sets_visa():
    async def render(url):
        return LinkResult("ok", url, "Data Engineer role. We will not sponsor visas. " * 10)
    verified, rejected = await run([posting()], lambda req: httpx.Response(200, text="<div id=root>"),
                                   renderer=render)
    assert not rejected
    assert verified[0].link_verified and verified[0].visa_sponsorship == "no"
