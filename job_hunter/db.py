"""SQLite storage. Only verified + scored postings reach `jobs`; failures go to `rejected`."""

import csv
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone

from job_hunter import config
from job_hunter.models import Rejection, ScoredPosting

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company TEXT NOT NULL,
    role TEXT NOT NULL,
    posted_date TEXT,
    link TEXT NOT NULL UNIQUE,
    applied INTEGER NOT NULL DEFAULT 0,
    sector TEXT NOT NULL,
    role_family TEXT,
    location TEXT,
    source TEXT,
    final_score REAL,
    keyword_score REAL,
    llm_score REAL,
    reason TEXT,
    visa_sponsorship TEXT,
    department TEXT,
    position_type TEXT,
    closing_date TEXT,
    description TEXT,
    first_seen_at TEXT NOT NULL,
    run_id TEXT
);
CREATE TABLE IF NOT EXISTS rejected (
    link TEXT, company TEXT, role TEXT, reason TEXT, run_id TEXT, seen_at TEXT
);
CREATE TABLE IF NOT EXISTS orgs (
    name TEXT PRIMARY KEY,
    sector TEXT NOT NULL,
    careers_url TEXT,
    recipe_json TEXT,            -- saved navigation steps / filtered results URL
    status TEXT DEFAULT 'active' -- active | pending | ignored
);
"""

EXPORT_COLUMNS = ["company", "role", "posted_date", "link", "applied", "final_score", "sector"]


@contextmanager
def connect():
    db_path = config.path(config.settings()["database"])
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def known_links() -> set[str]:
    with connect() as conn:
        return {r["link"] for r in conn.execute("SELECT link FROM jobs")}


def insert_jobs(scored: list[ScoredPosting], run_id: str) -> int:
    rows = []
    for s in scored:
        p = s.posting
        rows.append((
            p.company, p.role, p.posted_date and p.posted_date.isoformat(), p.link, p.sector,
            p.role_family, p.location, p.source, s.final_score, s.keyword_score, s.llm_score,
            s.reason, s.visa_sponsorship, p.department, p.position_type,
            p.closing_date and p.closing_date.isoformat(), p.description, _now(), run_id,
        ))
    with connect() as conn:
        cur = conn.executemany(
            """INSERT OR IGNORE INTO jobs (company, role, posted_date, link, sector, role_family,
               location, source, final_score, keyword_score, llm_score, reason, visa_sponsorship,
               department, position_type, closing_date, description, first_seen_at, run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        return cur.rowcount


def insert_rejected(rejected: list[Rejection], run_id: str) -> None:
    with connect() as conn:
        conn.executemany(
            "INSERT INTO rejected VALUES (?,?,?,?,?,?)",
            [(r.posting.link, r.posting.company, r.posting.role, r.reason, run_id, _now())
             for r in rejected],
        )


def mark_applied(job_id: int) -> bool:
    with connect() as conn:
        return conn.execute("UPDATE jobs SET applied = 1 WHERE id = ?", (job_id,)).rowcount > 0


def latest_rejected() -> list[sqlite3.Row]:
    with connect() as conn:
        return conn.execute(
            "SELECT reason, company, role, link FROM rejected "
            "WHERE run_id = (SELECT run_id FROM rejected ORDER BY seen_at DESC LIMIT 1) "
            "ORDER BY reason"
        ).fetchall()


def export_csv() -> str:
    out = config.path(config.settings()["csv_export"])
    with connect() as conn:
        rows = conn.execute(
            f"SELECT {', '.join(EXPORT_COLUMNS)} FROM jobs ORDER BY final_score DESC"
        ).fetchall()
    with open(out, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["Company", "Role", "Posted date", "Link", "Applied", "Score", "Sector"])
        w.writerows([tuple(r) for r in rows])
    return str(out)
