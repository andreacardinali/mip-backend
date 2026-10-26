from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


class MarketScreenerRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_ranking_inputs(
        self, *, algorithm_version: str | None, limit: int
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                """
                SELECT *
                FROM public.sp_market_screener_list_ranking_inputs(
                    CAST(:algorithm_version AS varchar),
                    CAST(:limit AS integer)
                )
            """
            ),
            {"algorithm_version": algorithm_version, "limit": limit},
        ).mappings()
        return [dict(row) for row in rows]

    def replace_ranking(
        self, *, algorithm_version: str, opportunities: list[dict[str, Any]]
    ) -> dict[str, Any]:
        value = self.db.execute(
            text(
                """
                SELECT public.sp_market_screener_replace_ranking(
                    CAST(:algorithm_version AS varchar),
                    CAST(:opportunities AS jsonb)
                )
            """
            ),
            {
                "algorithm_version": algorithm_version,
                "opportunities": json.dumps(opportunities),
            },
        ).scalar_one()
        return dict(value)
