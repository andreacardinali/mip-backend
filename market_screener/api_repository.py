from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class MarketScreenerApiRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_opportunities(
        self,
        *,
        user_id: str,
        origin: str | None = None,
        direction: str | None = None,
        verdict: str | None = None,
        portfolio_match: bool | None = None,
        consensus_direction: str | None = None,
        strategy_code: str | None = None,
        context_type: str | None = None,
        context_id: str | None = None,
        minimum_score: float = 0.0,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                """SELECT payload
            FROM public.sp_market_screener_list_filtered_opportunities(
            CAST(:user_id AS uuid),CAST(:origin AS varchar),CAST(:direction AS varchar),
            CAST(:verdict AS varchar),CAST(:portfolio_match AS boolean),CAST(:minimum_score AS numeric),
            CAST(:query AS text),CAST(:consensus_direction AS varchar),
            CAST(:strategy_code AS varchar),CAST(:context_type AS varchar),
            CAST(:context_id AS uuid),CAST(:limit AS integer)) payload"""
            ),
            {
                "user_id": user_id,
                "origin": origin,
                "direction": direction,
                "verdict": verdict,
                "portfolio_match": portfolio_match,
                "consensus_direction": consensus_direction,
                "strategy_code": strategy_code,
                "context_type": context_type,
                "context_id": context_id,
                "minimum_score": minimum_score,
                "query": query,
                "limit": limit,
            },
        ).scalars()
        return [dict(row) for row in rows]

    def get_opportunity(self, *, user_id: str, opportunity_id: str):
        p = self.db.execute(
            text(
                "SELECT public.sp_market_screener_enrich_consensus_payload(public.sp_market_screener_enrich_opportunity_payload(public.sp_market_screener_get_user_opportunity(CAST(:user_id AS uuid),CAST(:opportunity_id AS uuid)),CAST(:opportunity_id AS uuid)),CAST(:opportunity_id AS uuid))"
            ),
            {"user_id": user_id, "opportunity_id": opportunity_id},
        ).scalar_one_or_none()
        return dict(p) if p is not None else None

    def list_runs(self, limit=50):
        return [
            dict(x)
            for x in self.db.execute(
                text(
                    "SELECT payload FROM public.sp_market_screener_list_runs(CAST(:limit AS integer)) payload"
                ),
                {"limit": limit},
            ).scalars()
        ]

    def list_snapshots(self, limit=50):
        return [
            dict(x)
            for x in self.db.execute(
                text(
                    "SELECT payload FROM public.sp_market_screener_list_ranking_snapshots(CAST(:limit AS integer)) payload"
                ),
                {"limit": limit},
            ).scalars()
        ]

    def get_snapshot_changes(self, snapshot_id: str | None = None):
        payload = self.db.execute(
            text(
                "SELECT public.sp_market_screener_snapshot_changes("
                "CAST(:snapshot_id AS uuid))"
            ),
            {"snapshot_id": snapshot_id},
        ).scalar_one_or_none()
        return dict(payload) if payload is not None else None

    def list_summary_recipients(self) -> list[str]:
        return [
            str(user_id)
            for user_id in self.db.execute(
                text(
                    "SELECT user_id FROM public.sp_market_screener_summary_recipients()"
                )
            ).scalars()
        ]

    def list_snapshot_items(self, snapshot_id):
        return [
            dict(x)
            for x in self.db.execute(
                text(
                    "SELECT public.sp_market_screener_enrich_snapshot_consensus_payload(payload,(payload->>'ranking_snapshot_item_id')::uuid) payload FROM public.sp_market_screener_list_snapshot_items(CAST(:snapshot_id AS uuid)) payload"
                ),
                {"snapshot_id": snapshot_id},
            ).scalars()
        ]
