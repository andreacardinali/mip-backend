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
                SELECT payload
                FROM public.sp_market_screener_list_user_opportunities(
                    CAST(:user_id AS uuid),
                    CAST(:algorithm_version AS varchar),
                    CAST(:origin AS varchar),
                    CAST(:direction AS varchar),
                    CAST(:verdict AS varchar),
                    CAST(:portfolio_match AS boolean),
                    CAST(:minimum_score AS numeric),
                    CAST(:query AS text),
                    CAST(:limit AS integer)
                ) AS payload
                """
            ),
            {
                "user_id": user_id,
                "algorithm_version": algorithm_version,
                "origin": origin,
                "direction": direction,
                "verdict": verdict,
                "portfolio_match": portfolio_match,
                "minimum_score": minimum_score,
                "query": query,
                "limit": limit,
            },
        ).scalars()

        return [dict(payload) for payload in rows]

    def get_opportunity(
        self,
        *,
        user_id: str,
        opportunity_id: str,
    ) -> dict[str, Any] | None:
        payload = self.db.execute(
            text(
                """
                SELECT public.sp_market_screener_get_user_opportunity(
                    CAST(:user_id AS uuid),
                    CAST(:opportunity_id AS uuid)
                )
                """
            ),
            {
                "user_id": user_id,
                "opportunity_id": opportunity_id,
            },
        ).scalar_one_or_none()

        return dict(payload) if payload is not None else None
