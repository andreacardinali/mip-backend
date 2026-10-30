from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

from db import SessionLocal
from job_runtime.lock_logging import (
    analysis_lock_owner,
    lock_identity,
    log_lock_contention,
)
from market_screener.run_repository import MarketScreenerRunRepository

_analysis_locked: ContextVar[bool] = ContextVar("analysis_locked", default=False)


@contextmanager
def hold_incremental_analysis_lock(db, *, owner: str = "gui:screener"):
    original_bind = db.get_bind()
    with (
        original_bind.connect() as connection,
        lock_identity(connection, analysis_lock_owner(owner), session_scoped=True),
    ):
        db.bind = connection
        repository = MarketScreenerRunRepository(db)
        acquired = False
        try:
            acquired = repository.session_analysis_lock()
            if not acquired:
                log_lock_contention(
                    connection,
                    lock_name="market_screener_analysis",
                    owner=analysis_lock_owner(owner),
                )
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
        owner = analysis_lock_owner(f"{operation.__module__}.{operation.__name__}")
        with lock_identity(db.connection(), owner):
            if not MarketScreenerRunRepository(db).try_analysis_lock():
                log_lock_contention(
                    db.connection(), lock_name="market_screener_analysis", owner=owner
                )
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
