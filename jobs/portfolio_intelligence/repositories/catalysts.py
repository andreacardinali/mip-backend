import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def save_intelligence_event(
    db: Session,
    *,
    branch: str,
    event_key: str,
    entity_id: str,
    instrument_id: str,
    category: str,
    payload: dict[str, Any],
    evidence: dict[str, Any],
) -> None:
    try:
        db.execute(
            text(
                """
            SELECT public.sp_save_intelligence_event(
                :branch, :event_key, CAST(:entity_id AS uuid),
                CAST(:instrument_id AS uuid), :category,
                CAST(:payload AS jsonb), CAST(:evidence AS jsonb)
            )
        """
            ),
            {
                "branch": branch,
                "event_key": event_key,
                "entity_id": entity_id,
                "instrument_id": instrument_id,
                "category": category,
                "payload": json.dumps(payload, default=str),
                "evidence": json.dumps(evidence, default=str),
            },
        )
        db.commit()
    except Exception:
        db.rollback()
        raise


def recent_catalysts(
    db: Session,
    entity_ids: list[str],
    days: int = 30,
) -> dict[str, list[dict[str, Any]]]:
    normalized_ids = sorted({str(entity_id) for entity_id in entity_ids if entity_id})

    if not normalized_ids:
        return {}

    rows = (
        db.execute(
            text(
                """
                SELECT *
                FROM public.sp_get_recent_intelligence_catalysts(
                    CAST(:ids AS uuid[]),
                    :days
                )
                """
            ),
            {
                "ids": normalized_ids,
                "days": days,
            },
        )
        .mappings()
        .all()
    )

    result: dict[str, list[dict[str, Any]]] = {}

    for row in rows:
        entity_id = str(row["entity_id"])
        result.setdefault(entity_id, []).append(_catalyst_from_row(row))

    return result


def upsert_catalyst(
    db: Session,
    *,
    entity_id: str,
    source_symbol: str | None,
    event_subtype: str,
    event_date: date | datetime,
    status: str,
    importance: str,
    title: str,
    source: str,
    payload: dict[str, Any],
) -> str:
    catalyst_id = db.execute(
        text(
            """
            SELECT public.sp_upsert_catalyst(
                CAST(:entity_id AS uuid),
                :source_symbol,
                :event_subtype,
                CAST(:event_date AS timestamptz),
                :status,
                :importance,
                :title,
                :source,
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "entity_id": entity_id,
            "source_symbol": source_symbol,
            "event_subtype": event_subtype,
            "event_date": event_date,
            "status": status,
            "importance": importance,
            "title": title,
            "source": source,
            "payload": payload,
        },
    ).scalar_one()

    return str(catalyst_id)


def _catalyst_from_row(
    row: Mapping[str, Any],
) -> dict[str, Any]:
    catalyst_date = row["catalyst_date"]

    return {
        "catalystId": str(row["catalyst_id"]),
        "entityId": str(row["entity_id"]),
        "eventType": row["catalyst_type"],
        "eventDate": (catalyst_date.isoformat() if catalyst_date is not None else None),
        "status": row["status"],
        "importance": float(row["importance"] or 0),
        "title": row["title"],
        "source": row["source"],
        "symbols": [
            {
                key: symbol.get(key)
                for key in ("instrument_id", "symbol", "exchange", "currency")
            }
            for symbol in row["symbols"] or []
        ],
    }
