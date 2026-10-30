from contextvars import ContextVar
from typing import Any

REQUEST_ONLY_JOBS = {
    "market_screener_context_analysis": "jobs.market_screener_context_analysis.worker",
    "market_price_refresh_request": "jobs.market_price_refresh.request",
}

SUPERSEDED_JOBS = {
    "market_discovery_refresh": "market_screener_pipeline",
    "market_discovery_candidate_resolver": "market_screener_pipeline",
    "market_discovery_analysis_scan": "market_screener_pipeline",
    "market_analysis_scan": "market_screener_pipeline",
    "market_analysis_llm_enrichment": "market_screener_pipeline",
    "market_screener_ranking": "market_screener_pipeline",
    "refresh_entities_full": "refresh_entities_missing",
}

current_job_request: ContextVar[dict[str, Any] | None] = ContextVar(
    "current_job_request", default=None
)
