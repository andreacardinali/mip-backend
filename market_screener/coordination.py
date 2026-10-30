from __future__ import annotations

from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

from db import SessionLocal
from market_screener.run_repository import MarketScreenerRunRepository

_analysis_locked: ContextVar[bool] = ContextVar("analysis_locked", default=False)


def run_coordinated_analysis(operation: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    if _analysis_locked.get():
        return operation()
    db = SessionLocal()
    token = None
    try:
        if not MarketScreenerRunRepository(db).try_analysis_lock():
            return {"status": "SKIPPED", "reason": "analysis_already_running"}
        token = _analysis_locked.set(True)
        return operation()
    finally:
        if token is not None:
            _analysis_locked.reset(token)
        try:
            db.rollback()
        finally:
            db.close()
