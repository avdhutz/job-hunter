# Job Hunter

A [LangGraph](https://langchain-ai.github.io/langgraph/) pipeline that finds new job postings in **industry** and **academia**, verifies them, scores them against a resume, and stores them in SQLite. It only collects postings; it never applies.

> **Status: early.** The code is unit-tested with fakes and local fixtures. The browser agent and LLM scoring have not yet run against live sites. Expect rough edges.

## How it works

```
START -> [industry, academic] (parallel) -> verify -> score -> persist -> END
```

1. **Search.** Orgs on Greenhouse, Lever or Ashby use their public JSON API (no LLM). Other orgs are browsed by a Sonnet agent that opens the careers site and applies the job-category filter (academic orgs go to Staff positions). A cheap Haiku model then reads the list and each posting. Links come from the page DOM, never from model output.
2. **Recipes.** After a successful browse, the navigation steps are saved per org and replayed on later runs. The agent runs again only when a recipe breaks.
3. **Verify** (deterministic, no LLM): required fields, duplicates, role-family and seniority match, US location, posting age, link check (`httpx`, then headless Playwright), closed-posting markers, and the title appearing on the page. Rejections are stored with a reason.
4. **Score:** `final = (0.4 * keyword + 0.6 * haiku) * role_family_weight`. Role families in priority order: FDE > AI > ML > Data Engineering > Data Analyst > Data Science.
5. **Persist** to SQLite. Nothing is written on a dry run.

A per-run cost cap, per-org failure isolation and an end-of-run summary (counts, rejections, tokens, cost) are built in.

## Setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run playwright install chromium
cp .env.example .env    # add ANTHROPIC_API_KEY (and LANGSMITH_API_KEY for tracing)
```

Edit `config/settings.yaml` before running: set `resumes:` to your own resume PDFs. Orgs, filters, weights, skills and seniority rules are all in `config/`.

## Usage

```bash
uv run python -m job_hunter run --dry-run               # full pipeline, no DB writes
uv run python -m job_hunter run --only academic
uv run python -m job_hunter run --dry-run --max-cost 2  # override the cost cap
uv run python -m job_hunter export                      # writes data/jobs.csv
uv run python -m job_hunter rejected                    # why postings were dropped
uv run python -m job_hunter mark-applied <id>
uv run pytest -q                                        # tests (about a minute; launches Chromium)
uv run langgraph dev                                    # LangGraph Studio
```

Every LLM call goes through LangChain, so with `LANGSMITH_TRACING=true` each node, prompt and tool call is traced in LangSmith.

## Layout

- `job_hunter/graph.py`, `state.py`, `models.py`: main graph, state and data models
- `job_hunter/industry/`, `job_hunter/academic/`: per-sector nodes (`industry/ats.py` is the ATS shortcut)
- `job_hunter/browser/`: Playwright tools, the Sonnet navigator and the Haiku extractor
- `job_hunter/verify.py`, `score.py`, `db.py`, `budget.py`, `cli.py`
- `config/`: all tunables
- `docs/decisions.md`: design decisions and open questions
- `tests/`: unit tests and fixtures (no live-site tests)

## Roadmap

Planned as separate graphs in this repo, using the `jobs` table as the contract: a resume optimizer and an auto-apply agent (which will read only rows with `applied = 0 AND link_verified = 1`).
