import json
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session


class NotificationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def notification_exists(self, user_id: str, notification_key: str) -> bool:
        return bool(self.db.execute(
            text("SELECT sp_notification_exists(CAST(:user_id AS uuid), :key)"),
            {"user_id": user_id, "key": notification_key},
        ).scalar())

    def get_delivery_source(self, user_id: str, source_type: str) -> dict:
        row = self.db.execute(
            text("SELECT * FROM sp_get_user_notification_source(CAST(:user_id AS uuid), :source_type)"),
            {"user_id": user_id, "source_type": source_type},
        ).mappings().first()
        return dict(row or {})

    def upsert_notification(self, *, user_id: str, notification_key: str, branch: str,
                            entity_id: str | None, symbol: str | None, event_key: str | None,
                            severity: str, title: str, message: str, context: dict,
                            status: str, sent_at: datetime | None) -> None:
        self.db.execute(text("""
            SELECT sp_upsert_notification(
                CAST(:user_id AS uuid), :notification_key, :branch,
                CAST(:entity_id AS uuid), :symbol, :event_key, :severity,
                :title, :message, CAST(:context AS jsonb), :status, :sent_at
            )
        """), {"user_id": user_id, "notification_key": notification_key, "branch": branch,
                "entity_id": entity_id, "symbol": symbol, "event_key": event_key,
                "severity": severity, "title": title, "message": message,
                "context": json.dumps(context, default=str), "status": status, "sent_at": sent_at})

    def purge_notifications(self, days: int) -> int:
        return int(self.db.execute(text("SELECT sp_purge_notifications(:days)"), {"days": days}).scalar() or 0)
