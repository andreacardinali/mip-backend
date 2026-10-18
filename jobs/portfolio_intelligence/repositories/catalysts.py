from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from sqlalchemy.orm import Session

def recent_catalysts(db: Session, entity_ids: list[str], days: int = 30) -> dict[str, list[dict]]:
    if not entity_ids:
        return {}
    rows = db.execute(text("""
        SELECT entity_id, catalyst_type, catalyst_date, status, importance,
               title, source, raw_payload
        FROM catalysts
        WHERE entity_id = ANY(CAST(:ids AS uuid[]))
          AND catalyst_date >= :cutoff
        ORDER BY catalyst_date DESC
    """), {"ids": entity_ids, "cutoff": datetime.now(timezone.utc)-timedelta(days=days)}).mappings()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(str(r["entity_id"]), []).append({
            "eventType": r["catalyst_type"], "eventDate": r["catalyst_date"].isoformat(),
            "status": r["status"], "importance": float(r["importance"] or 0),
            "title": r["title"], "source": r["source"], "payload": r["raw_payload"]
        })
    return out
