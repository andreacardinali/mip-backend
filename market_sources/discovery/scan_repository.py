from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


class DiscoveryScanRepository:
    """Read scan candidates through market-discovery stored procedures."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def list_scan_candidates(
        self,
        *,
        snapshot_id: str | UUID | None = None,
        core_min_attention_score: float = 20,
        discovery_min_attention_score: float = 40,
        core_limit: int = 100,
        discovery_limit: int = 25,
    ) -> list[dict[str, Any]]:
        rows = self._db.execute(
            text(
                """
                SELECT *
                FROM public.sp_market_discovery_list_scan_candidates(
                    CAST(:snapshot_id AS uuid),
                    CAST(:core_min_attention_score AS numeric),
                    CAST(:discovery_min_attention_score AS numeric),
                    CAST(:core_limit AS integer),
                    CAST(:discovery_limit AS integer)
                )
                """
            ),
            {
                "snapshot_id": (str(snapshot_id) if snapshot_id is not None else None),
                "core_min_attention_score": core_min_attention_score,
                "discovery_min_attention_score": (discovery_min_attention_score),
                "core_limit": core_limit,
                "discovery_limit": discovery_limit,
            },
        ).mappings()

        return [dict(row) for row in rows]
