from job_hunter import budget
from job_hunter.models import JobPosting
from job_hunter.score import FitResult, keyword_score, score_postings


def posting(**kw) -> JobPosting:
    base = {"company": "Acme", "role": "ML Engineer", "link": "https://acme.com/1",
            "sector": "industry", "source": "greenhouse", "role_family": "ml",
            "description": "Python, SQL and machine learning with PyTorch"}
    return JobPosting(**{**base, **kw})


def test_keyword_score_bounds():
    assert keyword_score("") == 0
    assert 0 < keyword_score("We use Python") < 100
    core = "python sql llm rag langgraph langchain agents machine learning pytorch generative ai"
    assert keyword_score(core + " mlflow fastapi aws docker kubernetes pyspark airflow") == 100


def test_keyword_score_whole_words():
    assert keyword_score("javascript") == 0  # no "java" style substring hits
    assert keyword_score("sqlite") == 0       # "sql" must be a whole word


class FakeModel:
    def __init__(self, score=80, visa="unknown", fail_for=None):
        self.score, self.visa, self.fail_for = score, visa, fail_for

    async def ainvoke(self, messages):
        if self.fail_for and self.fail_for in messages[-1].content:
            raise RuntimeError("boom")
        return FitResult(fit_score=self.score, visa_sponsorship=self.visa, reason="ok",
                         matched_skills=["python"])


async def test_score_formula_and_sorting(monkeypatch):
    monkeypatch.setattr("job_hunter.resume.resume_text", lambda sector: "resume text")
    a = posting()                                                    # ml weight 0.90
    b = posting(link="https://acme.com/2", role_family="fde")        # fde weight 1.00
    scored = await score_postings([a, b], FakeModel(score=80))
    kw = keyword_score(a.description)
    assert scored[0].posting.role_family == "fde"
    assert scored[0].final_score == round((0.4 * kw + 0.6 * 80) * 1.0, 1)
    assert scored[1].final_score == round((0.4 * kw + 0.6 * 80) * 0.9, 1)


async def test_verify_visa_no_wins_and_llm_failure_is_isolated(monkeypatch):
    monkeypatch.setattr("job_hunter.resume.resume_text", lambda sector: "resume text")
    a = posting(visa_sponsorship="no")
    b = posting(link="https://acme.com/2", description="BOOM")
    scored = await score_postings([a, b], FakeModel(visa="yes", fail_for="BOOM"))
    by = {s.posting.link: s for s in scored}
    assert by["https://acme.com/1"].visa_sponsorship == "no"
    assert by["https://acme.com/2"].llm_score == 0
    assert "failed" in by["https://acme.com/2"].reason


async def test_min_score_filters(monkeypatch):
    from job_hunter import config
    monkeypatch.setattr("job_hunter.resume.resume_text", lambda sector: "x")
    s = dict(config.settings(), min_score=60)
    monkeypatch.setattr(config, "settings", lambda: s)
    assert await score_postings([posting()], FakeModel(score=10)) == []


def test_budget_cost_and_cap():
    b = budget.start_run(max_cost_usd=0.01)
    b.add_usage("claude-sonnet-4-5", 1000, 1000)  # 3/M in + 15/M out = 0.018
    assert round(b.cost_usd, 4) == 0.018 and b.exceeded
    b2 = budget.start_run()
    b2.add_usage("claude-haiku-5-5", 2000, 0, cache_read=1000)
    assert round(b2.cost_usd, 6) == round((1000 * 1.0 + 1000 * 0.1) / 1e6, 6)
