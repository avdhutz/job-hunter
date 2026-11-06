# Job Hunter

> **DRAFT — nothing here is tested yet.** This describes the intended design. Decisions are in `docs/decisions.md`. Treat everything below as provisional and check it against the code.

A LangGraph pipeline that finds new job postings in **industry** and **academia**, **verifies** them, **scores** them against my resume, and stores them in SQLite. Later an auto-apply step will read rows where `applied = 0`. For now the pipeline only pulls postings; it never applies.

Target role families, in priority order (weights are in `config/settings.yaml`): FDE > AI > ML > Data Engineering > Data Analyst > Data Science.

## Pipeline
```
START -> [industry, academic] (parallel) -> verify -> score -> persist -> END
```
- **Search flow, shared by both sectors** (`job_hunter/browser/agent.py`):
  1. Search "<org> careers" and open the official careers site.
  2. **Academic only:** go to **Staff positions** (staff roles only; no postdoc or faculty). Orgs with `staff_section: false` (Argonne) use their general job listing.
  3. Apply the site's **job category filter** (`category_filters` in `config/companies.yaml` / `config/academic.yaml`).
  4. List the postings, open each one, and submit it.
- **Industry shortcut:** orgs with `ats: greenhouse|lever|ashby` in `companies.yaml` use the public JSON API (`industry/ats.py`). No LLM is involved. Title, seniority and location filters run before link checks. Keep per-org counts, not a rejection row per non-matching title.
- **Recipes:** after the agent succeeds for an org, it saves the navigation steps, the filtered-results URL and page markers in the `orgs` table. Later runs replay the recipe and only fall back to the agent when it fails. A recipe fails on a step error or lost markers. An empty result with intact markers is a success. Zero postings for 3 runs in a row forces one agent run. If the agent fails too, set the org to `pending` and skip it.
- **Verify** (`verify.py`, deterministic, no LLM): nothing reaches `jobs` unless it passes:
  - required fields present
  - not a duplicate (links are normalized; checked against `jobs` only, rejected rows are re-checked each run)
  - title matches a role family and passes the seniority rules (no level marker, I, II, III and Senior allowed; level IV+, Staff, Lead, Principal, Director, Manager, Intern excluded)
  - location is US (whole-word state or country match; bare "Remote" accepted; multi-location passes if any is US)
  - posted within `max_age_days` (academic postings may pass on a future closing date instead). Undated postings are accepted with a blank date.
  - link check in two tiers: `httpx`, then a headless Playwright render (no LLM) when inconclusive. 404/410 are rejected. A link still blocked after tier 2 is kept with `link_verified = 0`.
  - posting is not closed
  - the role title appears on the page text (ATS postings skip this check)

  `visa_sponsorship = "no"` is set from a phrase list in config; it is a column, not a filter. Failures go to the `rejected` table with a reason.
- **Score** (`score.py`): `final = (0.4*keyword + 0.6*haiku) * role_family_weight`. The keyword score uses `config/skills.yaml`. The Haiku score compares against the resume for that sector (`resumes:` in settings). No minimum score yet (`min_score: 0`).
- **Run budget:** a per-run cost cap from config, about 3 agent orgs at a time, per-org failure isolation, and an end-of-run summary (counts, rejections by reason, tokens, cost).

## Stack
- Python 3.12, `uv`, LangGraph, `langchain-anthropic`, Playwright, httpx, SQLite
- Models (`config/settings.yaml`): `claude-sonnet-4-5` for the browsing agent, `claude-haiku-5-5` for scoring
- Tracing: LangSmith. Set `LANGSMITH_TRACING=true` and `LANGSMITH_API_KEY` in `.env`. Every node, prompt and tool call is traced. `langgraph dev` opens LangGraph Studio.

## Commands
```bash
uv sync                                   # install deps
uv run playwright install chromium        # one-time browser install
uv run python -m job_hunter run --dry-run # full pipeline, no DB writes
uv run python -m job_hunter run --only academic
uv run python -m job_hunter export        # data/jobs.csv (Company, Role, Posted date, Link, Applied, Score, Sector, Visa sponsorship, Link verified)
uv run python -m job_hunter rejected      # why postings were dropped in the last run
uv run python -m job_hunter mark-applied <id>
uv run pytest -q
uv run langgraph dev                      # LangGraph Studio
```

## Layout
- `job_hunter/graph.py`: main graph. `state.py` holds the LangGraph state, `models.py` the Pydantic models.
- `job_hunter/industry/`, `job_hunter/academic/`: per-sector subgraph nodes.
- `job_hunter/browser/`: Playwright tools and the Sonnet navigator agent.
- `job_hunter/verify.py`, `score.py`, `resume.py`, `db.py`, `cli.py`
- `config/`: all tunables (models, weights, keywords, orgs, filters, skills). Change these instead of hardcoding values.
- `data/`: `jobs.db`, `jobs.csv` (gitignored)

## Conventions
- All LLM work goes through LangChain/LangGraph so LangSmith traces it. Don't make raw SDK calls that bypass tracing.
- The LLM only navigates and extracts. Checks and filtering are deterministic Python.
- Postings must carry the exact URL the agent actually visited. Never construct URLs.
- DB writes happen only in `persist_node`, and are skipped when `dry_run` is set.
- Keep it simple: no new frameworks without a reason.
- Auto-apply (later) reads only `applied = 0 AND link_verified = 1`. A resume optimizer and auto-apply agent will live in this repo as separate graphs and CLI commands, with the `jobs` table as the contract.
- Tests use fixtures in `tests/fixtures/`; no live-site tests in `pytest`.

## Status
Skeleton only. Build order: config and model tweaks, `verify.py`, `industry/ats.py`, `score.py`, then `browser/tools.py` and `browser/agent.py`.
