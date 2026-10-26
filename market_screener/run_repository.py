from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class MarketScreenerRunRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def start(self) -> UUID:
        return self.db.execute(
            text("SELECT public.sp_market_screener_start_run()")
        ).scalar_one()

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
