"""Careers-site navigator agent (Sonnet + Playwright tools), traced in LangSmith.

Shared flow for every org:
  1. web search "<org> careers"  ->  open the official careers site
  2. (academic only) go to STAFF positions
  3. apply the site's job-category filter (or keyword search)
  4. list matching postings -> open each -> submit_postings(...)
After success, save a recipe (careers URL, filtered-results URL, filters) to the `orgs`
table so later runs replay it and only fall back to the agent if it breaks.
"""

from job_hunter.models import JobPosting, Sector

INDUSTRY_STEPS = """\
1. Search the web for "{org} careers" and open the company's official careers site.
2. Use the site's job category / team filter for: {categories}. If there is no filter, use its keyword search.
3. Collect the matching postings, open each one, and call submit_postings with exact URLs you visited."""

ACADEMIC_STEPS = """\
1. Search the web for "{org} careers" and open the university's official careers / HR jobs site.
2. Go to STAFF positions (not faculty or student jobs).
3. Use the job category filter for: {categories}.
4. Collect the matching postings, open each one, and call submit_postings with exact URLs you visited."""


async def navigate_org(org: dict, sector: Sector, categories: list[str]) -> list[JobPosting]:
    """Run the agent (or replay a saved recipe) for one org."""
    # TODO: create_react_agent(ChatAnthropic(model=settings models.agent), tools=[...])
    return []
