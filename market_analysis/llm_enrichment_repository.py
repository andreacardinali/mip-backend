from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text


class MarketAnalysisLLMEnrichmentRepository:
    def __init__(self, db) -> None:
        self.db = db

    def list_pending(
        self, *, enrichment_fingerprint: str, limit: int, min_interval_minutes: int = 30
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                """
                SELECT * FROM public.sp_market_analysis_list_pending_llm_enrichment(
                    CAST(:enrichment_fingerprint AS varchar), CAST(:limit AS integer),
                    NULL::uuid[], CAST(:min_interval_minutes AS integer)
                )
            """
            ),
            {
                "enrichment_fingerprint": enrichment_fingerprint,
                "limit": limit,
                "min_interval_minutes": min_interval_minutes,
            },
        ).mappings()
        return [dict(row) for row in rows]

    def list_pending_context(
        self,
        *,
        user_id: str,
        context_type: str,
        context_id: str,
        enrichment_fingerprint: str,
        limit: int,
        min_interval_minutes: int = 30,
    ) -> list[dict[str, Any]]:
        return list(
            self.db.execute(
                text(
                    """
                SELECT * FROM public.sp_market_analysis_list_pending_context_llm_enrichment(
                    CAST(:enrichment_fingerprint AS varchar), CAST(:user_id AS uuid),
                    CAST(:context_type AS varchar), CAST(:context_id AS uuid),
                    CAST(:limit AS integer), CAST(:min_interval_minutes AS integer)
                )
            """
                ),
                dict(
                    user_id=user_id,
                    context_type=context_type,
                    context_id=context_id,
                    enrichment_fingerprint=enrichment_fingerprint,
                    limit=limit,
                    min_interval_minutes=min_interval_minutes,
                ),
            ).scalars()
        )

    def apply(
        self,
        *,
        signal_id: str,
        enrichment_fingerprint: str,
        llm_score: float,
        llm_analysis: dict[str, Any],
        composite_score: float,
        input_signal: dict[str, Any],
        assessment_event_id: str | None = None,
    ) -> bool:
        value = self.db.execute(
            text(
                """
                SELECT public.sp_market_analysis_apply_lake_enrichment(
                    CAST(:signal_id AS uuid), CAST(:enrichment_fingerprint AS varchar),
                    CAST(:llm_score AS numeric), CAST(:llm_analysis AS jsonb),
                    CAST(:composite_score AS numeric), CAST(:assessment_event_id AS uuid),
                    CAST(:input_signal AS jsonb)
                )
            """
            ),
            {
                "signal_id": signal_id,
                "enrichment_fingerprint": enrichment_fingerprint,
                "llm_score": llm_score,
                "llm_analysis": json.dumps(llm_analysis),
                "composite_score": composite_score,
                "assessment_event_id": assessment_event_id,
                "input_signal": json.dumps(
                    {
                        key: value
                        for key, value in input_signal.items()
                        if key not in {"llm_analysis", "enrichment_fingerprint"}
                    },
                    default=str,
                    allow_nan=False,
                ),
            },
        ).scalar_one()
        return bool(value)
