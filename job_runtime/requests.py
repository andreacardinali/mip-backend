from contextvars import ContextVar
from typing import Any

REQUEST_ONLY_JOBS = {
    "market_screener_context_analysis": "jobs.market_screener_context_analysis.worker",
    "market_price_refresh_request": "jobs.market_price_refresh.request",
}

current_job_request: ContextVar[dict[str, Any] | None] = ContextVar(
    "current_job_request", default=None
)