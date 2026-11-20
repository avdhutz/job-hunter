"""Per-run token/cost tracking. A LangChain callback adds every LLM call's usage here, so the
cost cap and the end-of-run summary cover agent, extractor and scorer calls alike."""

from collections import defaultdict

from langchain_core.callbacks import BaseCallbackHandler

from job_hunter import config


class RunBudget:
    def __init__(self, max_cost_usd: float | None = None):
        self.max_cost_usd = (
            config.settings()["run_budget"]["max_cost_usd"] if max_cost_usd is None else max_cost_usd
        )
        self.tokens: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        self.web_searches = 0
        self.cost_usd = 0.0

    def add_usage(self, model: str, input_tokens: int, output_tokens: int,
                  cache_read: int = 0, cache_write: int = 0, web_searches: int = 0) -> None:
        pricing = config.settings()["pricing"]
        price = pricing.get(model) or max(
            (v for v in pricing.values() if isinstance(v, dict)), key=lambda v: v["input"])
        plain = max(input_tokens - cache_read - cache_write, 0)
        self.cost_usd += (
            plain * price["input"]
            + cache_read * price["input"] * pricing["cache_read"]
            + cache_write * price["input"] * pricing["cache_write"]
            + output_tokens * price["output"]
        ) / 1e6
        self.cost_usd += web_searches * pricing["web_search_per_1000"] / 1000
        t = self.tokens[model]
        t["input"] += input_tokens
        t["output"] += output_tokens
        self.web_searches += web_searches

    @property
    def exceeded(self) -> bool:
        return self.cost_usd >= self.max_cost_usd


_current = RunBudget()


def start_run(max_cost_usd: float | None = None) -> RunBudget:
    global _current
    _current = RunBudget(max_cost_usd)
    return _current


def current() -> RunBudget:
    return _current


class UsageCallback(BaseCallbackHandler):
    """Attach to every ChatAnthropic via callbacks=[UsageCallback()]."""

    def on_llm_end(self, response, **kwargs) -> None:
        for gens in response.generations:
            for g in gens:
                msg = getattr(g, "message", None)
                usage = getattr(msg, "usage_metadata", None)
                if not usage:
                    continue
                meta = getattr(msg, "response_metadata", {}) or {}
                details = usage.get("input_token_details") or {}
                searches = ((meta.get("usage") or {}).get("server_tool_use") or {}).get(
                    "web_search_requests", 0)
                current().add_usage(
                    meta.get("model_name") or meta.get("model", ""),
                    usage.get("input_tokens", 0), usage.get("output_tokens", 0),
                    details.get("cache_read", 0), details.get("cache_creation", 0), searches)
