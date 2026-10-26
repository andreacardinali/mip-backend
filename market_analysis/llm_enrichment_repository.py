from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text


class MarketAnalysisLLMEnrichmentRepository:
    def __init__(self, db) -> None:
        self.db = db

    def list_pending(
        self,
        *,
        prompt_version: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                """
                SELECT *
                FROM public.sp_market_analysis_list_pending_llm_enrichment(
                    CAST(:prompt_version AS varchar),
                    CAST(:limit AS integer)
                )
                """
            ),
            {
                "prompt_version": prompt_version,
                "limit": limit,
            },
        ).mappings()

        return [dict(row) for row in rows]

    def apply(
        self,
        *,
        signal_id: str,
        prompt_version: str,
        llm_score: float,
        llm_analysis: dict[str, Any],
        composite_score: float,
    ) -> bool:
        value = self.db.execute(
            text(
                """
                SELECT public.sp_market_analysis_apply_llm_enrichment(
                    CAST(:signal_id AS uuid),
                    CAST(:prompt_version AS varchar),
                    CAST(:llm_score AS numeric),
                    CAST(:llm_analysis AS jsonb),
                    CAST(:composite_score AS numeric)
                )
                """
            ),
            {
                "signal_id": signal_id,
                "prompt_version": prompt_version,
                "llm_score": llm_score,
                "llm_analysis": json.dumps(llm_analysis),
                "composite_score": composite_score,
            },
        ).scalar_one()

        return bool(value)
