from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class DiscoveryCandidateRepository:
    """Database adapter containing only discovery-candidate SP calls."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def build_candidates(
        self,
        *,
        snapshot_id: str | UUID | None = None,
    ) -> dict[str, Any]:
        value = self._db.execute(
            text(
                """
                SELECT public.sp_market_discovery_build_candidates(
                    CAST(:snapshot_id AS uuid)
                )
                """
            ),
            {"snapshot_id": (str(snapshot_id) if snapshot_id is not None else None)},
        ).scalar_one()

        return dict(value)

    def list_candidates(
        self,
        *,
        snapshot_id: str | UUID | None = None,
        resolution_status: str | None = None,
        origin: str | None = None,
        min_attention_score: float = 0,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self._db.execute(
            text(
                """
                SELECT *
                FROM public.sp_market_discovery_list_candidates(
                    CAST(:snapshot_id AS uuid),
                    :resolution_status,
                    :origin,
                    :min_attention_score,
                    :result_limit
                )
                """
            ),
            {
                "snapshot_id": (str(snapshot_id) if snapshot_id is not None else None),
                "resolution_status": resolution_status,
                "origin": origin,
                "min_attention_score": min_attention_score,
                "result_limit": limit,
            },
        ).mappings()

        return [dict(row) for row in rows]

    def get_candidate(
        self,
        candidate_id: str | UUID,
    ) -> dict[str, Any] | None:
        value = self._db.execute(
            text(
                """
                SELECT public.sp_market_discovery_get_candidate(
                    CAST(:candidate_id AS uuid)
                )
                """
            ),
            {"candidate_id": str(candidate_id)},
        ).scalar_one_or_none()

        return dict(value) if value else None

    def delete_expired_candidates(
        self,
        retention_days: int,
    ) -> int:
        return int(
            self._db.execute(
                text(
                    """
                    SELECT public.sp_market_discovery_delete_expired_candidates(
                        :retention_days
                    )
                    """
                ),
                {"retention_days": retention_days},
            ).scalar_one()
        )
