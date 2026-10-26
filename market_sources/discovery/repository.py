from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from market_sources.models import DiscoverySnapshot


class DiscoveryRepository:
    """Persist discovery data exclusively through PostgreSQL routines."""

    def __init__(self, db: Session) -> None:
        self._db = db

    def save_snapshot(self, snapshot: DiscoverySnapshot) -> UUID:
        items: list[dict[str, Any]] = []

        for candidate in snapshot.candidates:
            payload = asdict(candidate)
            payload["raw_payload"] = payload.pop(
                "source_payload",
                {},
            )
            items.append(payload)

        value = self._db.execute(
            text(
                """
                SELECT public.sp_market_discovery_create_snapshot(
                    :provider_code,
                    :source_type,
                    :source_name,
                    :observed_at,
                    :requested_limit,
                    :source_reference,
                    CAST(:source_metadata AS jsonb),
                    CAST(:items AS jsonb)
                )
                """
            ),
            {
                "provider_code": snapshot.provider_name,
                "source_type": snapshot.source_type.value,
                "source_name": (
                    f"{snapshot.provider_name} " f"{snapshot.source_type.value}"
                ),
                "observed_at": snapshot.observed_at,
                "requested_limit": (snapshot.effective_requested_limit),
                "source_reference": snapshot.source_reference,
                "source_metadata": json.dumps(
                    snapshot.source_metadata,
                    default=str,
                ),
                "items": json.dumps(
                    items,
                    default=str,
                ),
            },
        ).scalar_one()

        return UUID(str(value))

    def get_latest_snapshot(
        self,
        *,
        provider_code: str,
        source_type: str,
    ) -> dict[str, Any] | None:
        return self._db.execute(
            text(
                """
                SELECT public.sp_market_discovery_get_latest_snapshot(
                    :provider_code,
                    :source_type
                )
                """
            ),
            {
                "provider_code": provider_code,
                "source_type": source_type,
            },
        ).scalar_one_or_none()

    def list_snapshot_history(
        self,
        *,
        provider_code: str,
        source_type: str,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        rows = self._db.execute(
            text(
                """
                SELECT *
                FROM public.sp_market_discovery_list_snapshot_history(
                    :provider_code,
                    :source_type,
                    :result_limit
                )
                """
            ),
            {
                "provider_code": provider_code,
                "source_type": source_type,
                "result_limit": limit,
            },
        ).mappings()

        return [dict(row) for row in rows]

    def delete_expired_snapshots(
        self,
        retention_days: int,
    ) -> int:
        value = self._db.execute(
            text(
                """
                SELECT public.sp_market_discovery_delete_expired_snapshots(
                    :retention_days
                )
                """
            ),
            {"retention_days": retention_days},
        ).scalar_one()

        return int(value)
