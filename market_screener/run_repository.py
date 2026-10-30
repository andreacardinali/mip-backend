from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from job_runtime.run_logging import current_job_run_id


class MarketScreenerRunRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def try_analysis_lock(self) -> bool:
        return bool(
            self.db.execute(
                text("SELECT public.sp_market_screener_try_analysis_lock()")
            ).scalar_one()
        )

    def start(self) -> UUID:
        return self.db.execute(
            text(
                "SELECT public.sp_market_screener_start_job_run(CAST(:job_run_id AS uuid))"
            ),
            {
                "job_run_id": (
                    str(current_job_run_id.get()) if current_job_run_id.get() else None
                )
            },
        ).scalar_one()

    def fail_for_job(self, *, job_run_id: str, error_message: str) -> None:
        self.db.execute(
            text(
                "SELECT public.sp_market_screener_fail_job_run(CAST(:job_run_id AS uuid), :error)"
            ),
            {"job_run_id": job_run_id, "error": error_message[:1000]},
        )

    def session_analysis_lock(self, *, release: bool = False) -> bool:
        return bool(
            self.db.execute(
                text(
                    "SELECT public.sp_market_screener_session_analysis_lock(:release)"
                ),
                {"release": release},
            ).scalar_one()
        )

    def progress(self, *, run_id: UUID, phase_results: dict[str, Any]) -> None:
        self.db.execute(
            text(
                "SELECT public.sp_market_screener_update_progress(CAST(:run_id AS uuid), CAST(:phases AS jsonb))"
            ),
            {"run_id": str(run_id), "phases": json.dumps(phase_results, default=str)},
        )

    def context_progress(self, *, user_id: str, context_type: str, context_id: str):
        return self.db.execute(
            text(
                "SELECT public.sp_market_screener_context_progress(CAST(:user_id AS uuid), CAST(:context_type AS varchar), CAST(:context_id AS uuid))"
            ),
            {
                "user_id": user_id,
                "context_type": context_type,
                "context_id": context_id,
            },
        ).scalar_one_or_none()

    def complete(
        self,
        *,
        run_id: UUID,
        status: str,
        discovery_snapshot_id: str | None,
        scan_run_id: str | None,
        phase_results: dict[str, Any],
        error_message: str | None = None,
    ) -> None:
        self.db.execute(
            text(
                """SELECT public.sp_market_screener_complete_run(
            CAST(:run_id AS uuid), CAST(:status AS varchar), CAST(:discovery_snapshot_id AS uuid),
            CAST(:scan_run_id AS uuid), CAST(:phase_results AS jsonb), CAST(:error_message AS text))"""
            ),
            {
                "run_id": str(run_id),
                "status": status,
                "discovery_snapshot_id": discovery_snapshot_id,
                "scan_run_id": scan_run_id,
                "phase_results": json.dumps(phase_results, default=str),
                "error_message": error_message,
            },
        )

    def create_snapshot(self, *, run_id: UUID) -> UUID:
        return self.db.execute(
            text(
                "SELECT public.sp_market_screener_create_ranking_snapshot(CAST(:run_id AS uuid))"
            ),
            {"run_id": str(run_id)},
        ).scalar_one()
