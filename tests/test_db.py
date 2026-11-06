from job_hunter import config, db
from job_hunter.models import JobPosting, ScoredPosting


def test_insert_is_idempotent(tmp_path, monkeypatch):
    s = dict(config.settings(), database=str(tmp_path / "t.db"))
    monkeypatch.setattr(config, "settings", lambda: s)
    p = JobPosting(company="Acme", role="FDE", link="https://x/1", sector="industry", source="test")
    sp = ScoredPosting(posting=p, keyword_score=50, llm_score=60, final_score=56)
    assert db.insert_jobs([sp], "r1") == 1
    assert db.insert_jobs([sp], "r2") == 0
    assert db.known_links() == {"https://x/1"}
