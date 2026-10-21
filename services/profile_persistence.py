from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def pin_profile_to_listing(
    profile: dict[str, Any],
    *,
    symbol: str,
    exchange: str | None = None,
    currency: str | None = None,
) -> dict[str, Any]:
    """Keep provider enrichment from silently switching the selected listing."""
    pinned = dict(profile)
    pinned["symbol"] = str(symbol).strip().upper()
    if exchange:
        pinned["exchange"] = str(exchange).strip().upper()
    if currency:
        pinned["currency"] = str(currency).strip().upper()
    return pinned


def upsert_asset_profile(
    db: Session,
    profile: dict[str, Any],
    *,
    entity_id=None,
    instrument_id=None,
    create_if_missing: bool = True,
):
    """Persist entity, listing, names and identities in one DB transaction."""
    return db.execute(
        text(
            """
            SELECT *
            FROM sp_upsert_asset_profile(
                CAST(:payload AS jsonb),
                CAST(:entity_id AS uuid),
                CAST(:instrument_id AS uuid),
                :create_if_missing
            )
            """
        ),
        {
            "payload": json.dumps(profile, default=str),
            "entity_id": entity_id,
            "instrument_id": instrument_id,
            "create_if_missing": create_if_missing,
        },
    ).first()
