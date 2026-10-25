from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from market_analysis.models import SignalCandidate, SignalStatus


class MarketAnalysisRepository:
    """Database adapter containing only calls to market-analysis procedures."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def upsert_universe(
        self,
        *,
        code: str,
        name: str,
        universe_type: str,
        source_system_code: str | None,
        active: bool = True,
        config: dict[str, Any] | None = None,
    ) -> str:
        return str(
            self.db.execute(
                text(
                    """
            SELECT public.sp_market_analysis_upsert_universe(
                :code, :name, :universe_type, :source_system_code,
                :active, CAST(:config AS jsonb))
        """
                ),
                {
                    "code": code,
                    "name": name,
                    "universe_type": universe_type,
                    "source_system_code": source_system_code,
                    "active": active,
                    "config": json.dumps(config or {}),
                },
            ).scalar_one()
        )

    def get_universe(self, *, code: str) -> dict[str, Any] | None:
        row = (
            self.db.execute(
                text(
                    """
            SELECT * FROM public.sp_market_analysis_get_universe(:code)
        """
                ),
                {"code": code},
            )
            .mappings()
            .one_or_none()
        )
        return dict(row) if row else None

    def resolve_universe_symbols(self, *, symbols: Sequence[str]) -> dict[str, str]:
        rows = self.db.execute(
            text(
                """
            SELECT * FROM public.sp_market_analysis_resolve_universe_symbols(
                CAST(:symbols AS jsonb))
        """
            ),
            {"symbols": json.dumps(list(symbols))},
        ).mappings()
        return {str(row["requested_symbol"]): str(row["instrument_id"]) for row in rows}

    def replace_universe_members(
        self, *, universe_id: str, members: Sequence[dict[str, Any]], effective_at: str
    ) -> dict[str, Any]:
        result = self.db.execute(
            text(
                """
            SELECT public.sp_market_analysis_replace_universe_members(
                CAST(:universe_id AS uuid), CAST(:members AS jsonb),
                CAST(:effective_at AS timestamptz))
        """
            ),
            {
                "universe_id": universe_id,
                "members": json.dumps(list(members)),
                "effective_at": effective_at,
            },
        ).scalar_one()
        return dict(result)

    def list_universe_members(self, *, universe_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                """
            SELECT * FROM public.sp_market_analysis_list_universe_members(
                CAST(:universe_id AS uuid))
        """
            ),
            {"universe_id": universe_id},
        ).mappings()
        return [dict(row) for row in rows]

    def start_scan_run(
        self, *, universe_id: str, algorithm_version: str, config: dict[str, Any]
    ) -> str:
        return str(
            self.db.execute(
                text(
                    """
            SELECT public.sp_market_analysis_start_scan_run(
                CAST(:universe_id AS uuid), :algorithm_version,
                CAST(:config AS jsonb))
        """
                ),
                {
                    "universe_id": universe_id,
                    "algorithm_version": algorithm_version,
                    "config": json.dumps(config),
                },
            ).scalar_one()
        )

    def finish_scan_run(
        self, *, run_id: str, status: str, summary: dict[str, Any]
    ) -> bool:
        return bool(
            self.db.execute(
                text(
                    """
            SELECT public.sp_market_analysis_finish_scan_run(
                CAST(:run_id AS uuid), :status, CAST(:summary AS jsonb))
        """
                ),
                {"run_id": run_id, "status": status, "summary": json.dumps(summary)},
            ).scalar_one()
        )

    def record_scan_failure(
        self, *, run_id: str, instrument_id: str, symbol: str, reason: str
    ) -> str:
        return str(
            self.db.execute(
                text(
                    """
            SELECT public.sp_market_analysis_record_scan_failure(
                CAST(:run_id AS uuid), CAST(:instrument_id AS uuid),
                :symbol, :reason)
        """
                ),
                {
                    "run_id": run_id,
                    "instrument_id": instrument_id,
                    "symbol": symbol,
                    "reason": reason,
                },
            ).scalar_one()
        )

    def upsert_signal(
        self,
        *,
        instrument_id: str,
        universe_id: str | None,
        candidate: SignalCandidate,
        scores: dict[str, float | None],
        algorithm_version: str,
        prompt_version: str | None = None,
        llm_analysis: dict[str, Any] | None = None,
    ) -> str:
        payload = candidate.to_payload()
        payload.update(
            {
                "instrument_id": instrument_id,
                "universe_id": universe_id,
                "scores": scores,
                "algorithm_version": algorithm_version,
                "prompt_version": prompt_version,
                "llm_analysis": llm_analysis,
            }
        )
        return str(
            self.db.execute(
                text(
                    "SELECT public.sp_market_analysis_upsert_signal(CAST(:payload AS jsonb))"
                ),
                {"payload": json.dumps(payload)},
            ).scalar_one()
        )

    def update_signal_status(
        self, *, signal_id: str, status: SignalStatus, close_reason: str | None = None
    ) -> bool:
        return bool(
            self.db.execute(
                text(
                    """
            SELECT public.sp_market_analysis_update_signal_status(
                CAST(:signal_id AS uuid), :status, :close_reason)
        """
                ),
                {
                    "signal_id": signal_id,
                    "status": status.value,
                    "close_reason": close_reason,
                },
            ).scalar_one()
        )
