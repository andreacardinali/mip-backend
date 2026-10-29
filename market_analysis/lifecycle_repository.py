from __future__ import annotations

import json

from sqlalchemy import text


class SignalLifecycleRepository:
    def __init__(self, db):
        self.db = db

    def reconcile(self, signal_id: str, stability_minutes: int = 30) -> dict:
        value = self.db.execute(
            text(
                "SELECT public.sp_market_analysis_reconcile_signal_lifecycle(CAST(:id AS uuid),CAST(:minutes AS integer))"
            ),
            {"id": signal_id, "minutes": stability_minutes},
        ).scalar_one()
        return dict(value)

    def expire_evaluated(
        self, scopes: list[dict], signal_ids: list[str], stability_minutes: int = 30
    ) -> int:
        if not scopes:
            return 0
        return int(
            self.db.execute(
                text(
                    "SELECT public.sp_market_analysis_expire_evaluated_signals("
                    "CAST(:scopes AS jsonb),CAST(:signal_ids AS uuid[]),CAST(:minutes AS integer))"
                ),
                {
                    "scopes": json.dumps(scopes),
                    "signal_ids": signal_ids,
                    "minutes": stability_minutes,
                },
            ).scalar_one()
        )

    def expire_missing(self, observed_before, stability_minutes: int = 30) -> int:
        return int(
            self.db.execute(
                text(
                    "SELECT public.sp_market_analysis_close_missing_signals(CAST(:observed AS timestamptz),CAST(:minutes AS integer))"
                ),
                {"observed": observed_before, "minutes": stability_minutes},
            ).scalar_one()
        )

    def list_events(
        self, instrument_id: str, strategy_code: str | None = None, limit: int = 100
    ) -> list[dict]:
        rows = self.db.execute(
            text(
                "SELECT payload FROM public.sp_market_analysis_list_signal_events(CAST(:instrument AS uuid),CAST(:strategy AS varchar),CAST(:limit AS integer)) payload"
            ),
            {"instrument": instrument_id, "strategy": strategy_code, "limit": limit},
        ).scalars()
        return [dict(x) for x in rows]

    def list_performance(
        self,
        instrument_id: str | None = None,
        strategy_code: str | None = None,
        status: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        rows = self.db.execute(
            text(
                "SELECT payload FROM public.sp_market_analysis_list_signal_performance("
                "CAST(:instrument AS uuid),CAST(:strategy AS varchar),CAST(:status AS varchar),"
                "CAST(:limit AS integer),CAST(:offset AS integer)) payload"
            ),
            {
                "instrument": instrument_id,
                "strategy": strategy_code,
                "status": status,
                "limit": limit,
                "offset": offset,
            },
        ).scalars()
        return [dict(row) for row in rows]
