from __future__ import annotations

import json
from typing import Any
from sqlalchemy import text
from sqlalchemy.orm import Session


class MarketScreenerRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_ranking_inputs(self, *, limit: int) -> list[dict[str, Any]]:
        rows = self.db.execute(text(
            "SELECT * FROM public.sp_market_screener_list_ranking_inputs(CAST(:limit AS integer))"
        ), {"limit": limit}).mappings()
        return [dict(x) for x in rows]

    def replace_ranking(self, *, opportunities: list[dict[str, Any]]) -> dict[str, Any]:
        value = self.db.execute(text(
            "SELECT public.sp_market_screener_replace_ranking(CAST(:opportunities AS jsonb))"
        ), {"opportunities": json.dumps(opportunities)}).scalar_one()
        return dict(value)

    def apply_consensus(self, *, opportunities: list[dict[str, Any]]) -> dict[str, Any]:
        value = self.db.execute(text(
            "SELECT public.sp_market_screener_apply_consensus(CAST(:opportunities AS jsonb))"
        ), {"opportunities": json.dumps(opportunities)}).scalar_one()
        return dict(value)
