from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class MarketScreenerApiRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_opportunities(
        self,
        *,
        user_id: str,
        algorithm_version: str | None = None,
        origin: str | None = None,
        direction: str | None = None,
        verdict: str | None = None,
        portfolio_match: bool | None = None,
        minimum_score: float = 0.0,
        query: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                """
            SELECT public.sp_market_screener_enrich_opportunity_payload(
                payload,
                (payload->>'opportunity_id')::uuid
            ) AS payload
            FROM public.sp_market_screener_list_user_opportunities(
                CAST(:user_id AS uuid), CAST(:algorithm_version AS varchar),
                CAST(:origin AS varchar), CAST(:direction AS varchar),
                CAST(:verdict AS varchar), CAST(:portfolio_match AS boolean),
                CAST(:minimum_score AS numeric), CAST(:query AS text),
                CAST(:limit AS integer)
            ) AS payload
        """
            ),
            locals(),
        ).scalars()
        return [dict(payload) for payload in rows]

    def get_opportunity(
        self, *, user_id: str, opportunity_id: str
    ) -> dict[str, Any] | None:
        payload = self.db.execute(
            text(
                """
            SELECT public.sp_market_screener_enrich_opportunity_payload(
                public.sp_market_screener_get_user_opportunity(
                    CAST(:user_id AS uuid), CAST(:opportunity_id AS uuid)
                ),
                CAST(:opportunity_id AS uuid)
            )
        """
            ),
            {"user_id": user_id, "opportunity_id": opportunity_id},
        ).scalar_one_or_none()
        return dict(payload) if payload is not None else None

    def list_runs(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                """
            SELECT payload FROM public.sp_market_screener_list_runs(CAST(:limit AS integer)) AS payload
        """
            ),
            {"limit": limit},
        ).scalars()
        return [dict(payload) for payload in rows]

    def list_snapshots(self, limit: int = 50) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                """
            SELECT payload FROM public.sp_market_screener_list_ranking_snapshots(CAST(:limit AS integer)) AS payload
        """
            ),
            {"limit": limit},
        ).scalars()
        return [dict(payload) for payload in rows]

    def list_snapshot_items(self, snapshot_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                """
            SELECT payload FROM public.sp_market_screener_list_snapshot_items(CAST(:snapshot_id AS uuid)) AS payload
        """
            ),
            {"snapshot_id": snapshot_id},
        ).scalars()
        return [dict(payload) for payload in rows]
