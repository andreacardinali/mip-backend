from collections.abc import Mapping
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session


def recent_catalysts(
    db: Session,
    entity_ids: list[str],
    days: int = 30,
) -> dict[str, list[dict[str, Any]]]:
    if not entity_ids:
        return {}
    rows = (
        db.execute(
            text(
                "SELECT * FROM " "sp_get_recent_catalysts(CAST(:ids AS uuid[]), :days)"
            ),
            {"ids": entity_ids, "days": days},
        )
        .mappings()
        .all()
    )

    result: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        result.setdefault(str(row["entity_id"]), []).append(_catalyst_from_row(row))
    return result


def _catalyst_from_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "eventType": row["catalyst_type"],
        "eventDate": row["catalyst_date"].isoformat(),
        "status": row["status"],
        "importance": float(row["importance"] or 0),
        "title": row["title"],
        "source": row["source"],
        "payload": row["raw_payload"],
    }
