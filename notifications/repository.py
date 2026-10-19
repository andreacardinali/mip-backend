import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from notifications.models import NotificationDelivery


class NotificationRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def resolve_source_id(
        self,
        *,
        user_id: str,
        channel: str,
    ) -> str | None:
        value = self.db.execute(
            text(
                """
                SELECT source_id
                FROM sp_get_user_notification_source(
                    CAST(:user_id AS uuid),
                    :channel
                )
                LIMIT 1
                """
            ),
            {
                "user_id": user_id,
                "channel": channel.upper(),
            },
        ).scalar()
        return str(value) if value else None

    def enqueue(
        self,
        *,
        user_id: str,
        source_id: str,
        channel: str,
        notification_key: str,
        notification_type: str,
        entity_id: str | None,
        symbol: str | None,
        event_key: str | None,
        severity: str,
        title: str,
        message: str,
        context: dict[str, Any],
    ) -> str | None:
        value = self.db.execute(
            text(
                """
                SELECT sp_enqueue_notification(
                    CAST(:user_id AS uuid),
                    CAST(:source_id AS uuid),
                    :channel,
                    :notification_key,
                    :notification_type,
                    CAST(:entity_id AS uuid),
                    :symbol,
                    :event_key,
                    :severity,
                    :title,
                    :message,
                    CAST(:context AS jsonb)
                )
                """
            ),
            {
                "user_id": user_id,
                "source_id": source_id,
                "channel": channel.upper(),
                "notification_key": notification_key,
                "notification_type": notification_type,
                "entity_id": entity_id,
                "symbol": symbol,
                "event_key": event_key,
                "severity": severity,
                "title": title,
                "message": message,
                "context": json.dumps(context, default=str),
            },
        ).scalar()
        return str(value) if value else None

    def release_stale(self, processing_timeout_seconds: int) -> int:
        value = self.db.execute(
            text("SELECT sp_release_stale_notifications(:timeout_seconds)"),
            {"timeout_seconds": processing_timeout_seconds},
        ).scalar()
        return int(value or 0)

    def claim(self, limit: int) -> list[NotificationDelivery]:
        rows = (
            self.db.execute(
                text("SELECT * FROM sp_claim_notifications(:limit)"),
                {"limit": limit},
            )
            .mappings()
            .all()
        )

        return [
            NotificationDelivery(
                notification_id=str(row["notification_id"]),
                user_id=str(row["user_id"]),
                source_id=str(row["source_id"]),
                channel=str(row["channel"]),
                notification_key=str(row["notification_key"]),
                notification_type=str(row["notification_type"]),
                severity=str(row["severity"]),
                title=str(row["title"]),
                message=str(row["message"]),
                context=dict(row["context"] or {}),
                source_external_id=(
                    str(row["source_external_id"])
                    if row["source_external_id"] is not None
                    else None
                ),
                source_config=dict(row["source_config"] or {}),
                attempt_count=int(row["attempt_count"]),
            )
            for row in rows
        ]

    def mark_sent(
        self,
        *,
        notification_id: str,
        provider_message_id: str | None,
    ) -> None:
        self.db.execute(
            text(
                """
                SELECT sp_mark_notification_sent(
                    CAST(:notification_id AS uuid),
                    :provider_message_id
                )
                """
            ),
            {
                "notification_id": notification_id,
                "provider_message_id": provider_message_id,
            },
        )

    def mark_failed(
        self,
        *,
        notification_id: str,
        error: str,
        max_retries: int,
        retry_base_seconds: int,
    ) -> str:
        value = self.db.execute(
            text(
                """
                SELECT sp_mark_notification_failed(
                    CAST(:notification_id AS uuid),
                    :error,
                    :max_retries,
                    :retry_base_seconds
                )
                """
            ),
            {
                "notification_id": notification_id,
                "error": error[:4000],
                "max_retries": max_retries,
                "retry_base_seconds": retry_base_seconds,
            },
        ).scalar()
        return str(value)

    def purge(self, retention_days: int) -> int:
        value = self.db.execute(
            text("SELECT sp_purge_notifications(:days)"),
            {"days": retention_days},
        ).scalar()
        return int(value or 0)
