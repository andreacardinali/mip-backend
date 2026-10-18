from typing import Any

from sqlalchemy.orm import Session

from notifications.repository import NotificationRepository


class NotificationQueue:
    """Shared producer API used by scheduler jobs and backend routes."""

    def __init__(
        self,
        db: Session,
        *,
        enabled: bool = True,
        default_channel: str = "TELEGRAM",
    ) -> None:
        self.repo = NotificationRepository(db)
        self.enabled = enabled
        self.default_channel = default_channel.upper()

    def enqueue(
        self,
        *,
        user_id: str,
        notification_key: str,
        notification_type: str,
        entity_id: str | None,
        symbol: str | None,
        event_key: str | None,
        severity: str,
        title: str,
        message: str,
        context: dict[str, Any],
        channel: str | None = None,
    ) -> bool:
        if not self.enabled:
            return False

        selected_channel = (channel or self.default_channel).upper()
        source_id = self.repo.resolve_source_id(
            user_id=user_id,
            channel=selected_channel,
        )

        if not source_id:
            return False

        notification_id = self.repo.enqueue(
            user_id=user_id,
            source_id=source_id,
            channel=selected_channel,
            notification_key=notification_key,
            notification_type=notification_type,
            entity_id=entity_id,
            symbol=symbol,
            event_key=event_key,
            severity=severity,
            title=title,
            message=message,
            context=context,
        )
        return notification_id is not None
