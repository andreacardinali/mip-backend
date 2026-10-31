import json
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from market_time.normalization import as_utc
from services.event_contract import EventEnvelope, stable_event_key


def save_event(db: Session, event: EventEnvelope) -> str:
    return str(
        db.execute(
            text("SELECT public.sp_save_event(CAST(:envelope AS jsonb))"),
            {"envelope": json.dumps(event.as_payload(), allow_nan=False)},
        ).scalar_one()
    )


def _instant(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return as_utc(value)


def _catalyst_identity(row: dict[str, Any]) -> tuple[str, str]:
    payload = row["payload"]
    subject = row.get("symbol") or row.get("entity_id")
    subtype = row["entity_subtype"]
    provider_id = next(
        (
            payload[key]
            for key in ("eventId", "id")
            if payload.get(key) not in (None, "")
        ),
        None,
    )
    year = payload.get("fiscalYear")
    period = payload.get("fiscalQuarter") or payload.get("fiscalPeriod")
    if provider_id is not None:
        identity, strategy = ("provider", str(provider_id)), "provider_id"
    elif "EARNING" in subtype.upper() and year is not None and period is not None:
        identity, strategy = ("fiscal", str(year), str(period)), "fiscal_period"
    else:
        value = row["entity_date"]
        day = as_utc(value).date() if isinstance(value, datetime) else value
        identity, strategy = ("date", day.isoformat()), "date_fallback"
    return stable_event_key(subject, subtype, identity), strategy


def save_news_article(
    db: Session, *, entity_id: str, payload: dict[str, Any]
) -> dict[str, Any]:
    result = db.execute(
        text(
            """
            SELECT public.sp_news_upsert_article(
                CAST(:entity_id AS uuid), CAST(:payload AS jsonb)
            )
            """
        ),
        {"entity_id": entity_id, "payload": json.dumps(payload, ensure_ascii=False)},
    ).scalar_one()
    return result if isinstance(result, dict) else json.loads(result)


def save_news_scores(db: Session, scores: list[dict[str, Any]]) -> int:
    return int(
        db.execute(
            text("SELECT public.sp_news_update_entity_scores(CAST(:scores AS jsonb))"),
            {"scores": json.dumps(scores, separators=(",", ":"))},
        ).scalar_one()
    )


def save_catalyst(db: Session, row: dict[str, Any]) -> Any:
    value = row["entity_date"]
    instant = as_utc(value) if isinstance(value, datetime) else None
    if not isinstance(value, date):
        raise ValueError("Catalyst requires a date or aware instant")
    payload = dict(row["payload"])
    if instant is None:
        payload["date"] = value.isoformat()
    event_key, strategy = _catalyst_identity(row)
    return save_event(
        db,
        EventEnvelope(
            event_type="CATALYST",
            stage="ACQUIRED",
            source=row["source"],
            event_key=event_key,
            source_event_id=next(
                (
                    str(payload[key])
                    for key in ("eventId", "id")
                    if payload.get(key) not in (None, "")
                ),
                None,
            ),
            occurred_at=instant,
            event_day=instant.date() if instant else value,
            subjects=[
                {
                    "symbol": row.get("symbol"),
                    "entity_id": row.get("entity_id"),
                }
            ],
            payload={
                "subtype": row["entity_subtype"],
                "status": row["status"],
                "importance": row["importance"],
                "title": row["title"],
                "details": payload,
            },
            raw_payload=dict(row["payload"]),
            provenance={"identity_strategy": strategy},
        ),
    )


def save_filing(db: Session, row: dict[str, Any]) -> None:
    accepted_at = as_utc(row["accepted_at"])
    accession = row.get("accession_number")
    save_event(
        db,
        EventEnvelope(
            event_type="FILING",
            stage="ACQUIRED",
            source=row["source"],
            event_key=stable_event_key(
                row["cik"],
                accession or (row["filing_type"], accepted_at),
            ),
            source_event_id=accession,
            occurred_at=accepted_at,
            event_day=row["filing_date"],
            subjects=[{"symbol": row["symbol"], "cik": row["cik"]}],
            payload={
                key: value
                for key, value in row.items()
                if key not in {"payload", "source"}
            },
            raw_payload=row["payload"],
            provenance={
                "identity_strategy": (
                    "accession" if accession else "accepted_at_fallback"
                )
            },
        ),
    )


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
    normalized = {
        key: value
        for key, value in payload.items()
        if key
        not in {
            "daysUntilEvent",
            "eventKey",
            "sources",
            "relatedSymbols",
            "name",
            "symbol",
            "entity_id",
            "observedDate",
            "marketEvidence",
        }
    }
    event_date = normalized.get("eventDate")
    event_day = (
        date.fromisoformat(event_date) if isinstance(event_date, str) else event_date
    )
    if branch == "DAILY_MOVER":
        event_day = (
            date.fromisoformat(event_key.split("|")[1]) if "|" in event_key else None
        )
        lake_key = stable_event_key(instrument_id, event_day or event_key)
    elif branch == "UPCOMING_CATALYST" and event_day:
        lake_key, _ = _catalyst_identity(
            {
                "entity_id": entity_id,
                "entity_subtype": normalized["eventType"],
                "entity_date": event_day,
                "payload": normalized,
            }
        )
    else:
        lake_key = event_key
    save_event(
        db,
        EventEnvelope(
            event_type=branch,
            stage="VALIDATED",
            source="COLLECTION_RESEARCH",
            event_key=lake_key,
            event_day=event_day,
            observed_at=(
                datetime.fromtimestamp(payload["observedDate"], UTC)
                if branch == "DAILY_MOVER" and payload.get("observedDate") is not None
                else None
            ),
            subjects=[{"entity_id": entity_id, "instrument_id": instrument_id}],
            payload=normalized,
            evidence={
                key: value for key, value in evidence.items() if key != "marketEvidence"
            },
            raw_payload=payload,
            provenance={
                "branch": branch,
                "category": category,
                "projection_event_key": event_key,
                "marketEvidence": evidence.get("marketEvidence"),
            },
        ),
    )
