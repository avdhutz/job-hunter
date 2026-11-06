"""Playwright tools the Sonnet agent uses to drive JS-heavy careers sites
(Workday, PeopleAdmin, Taleo, custom pages).

Tools: goto, snapshot, click, fill, select_option, submit_postings.
`snapshot` returns visible text + numbered links/buttons/selects so the model can act on them.
"""

# TODO: one shared async Playwright browser per run; wrap each action as a LangChain @tool
