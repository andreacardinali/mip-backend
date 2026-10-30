from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from db import SessionLocal
from market_screener.run_repository import MarketScreenerRunRepository

_analysis_locked: ContextVar[bool] = ContextVar("analysis_locked", default=False)


@contextmanager
def hold_incremental_analysis_lock(db):
    original_bind = db.get_bind()
    with original_bind.connect() as connection:
        db.bind = connection
        repository = MarketScreenerRunRepository(db)
        acquired = False
        try:
            acquired = repository.session_analysis_lock()
            if not acquired:
                raise ValueError("Analysis already running")
            yield
        finally:
            try:
                db.rollback()
                if acquired:
                    repository.session_analysis_lock(release=True)
                    db.commit()
            except Exception:
                connection.invalidate()
                raise
            finally:
                db.bind = original_bind


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
