from typing import Any

from sqlalchemy.orm import Session

from notifications.models import EnqueueResult, EnqueueStatus
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
    ) -> EnqueueResult:
        if not self.enabled:
            return EnqueueResult(EnqueueStatus.DISABLED)

        fallback_channel = (channel or self.default_channel).upper()

        # Compatibility path for existing test doubles and external callers
        # that still expose the pre-preferences repository interface.
        resolve_target = getattr(self.repo, "resolve_target", None)
        if resolve_target is None:
            source_id = self.repo.resolve_source_id(
                user_id=user_id,
                channel=fallback_channel,
            )
            if not source_id:
                return EnqueueResult(EnqueueStatus.SOURCE_NOT_FOUND)
            target_source_id = source_id
            target_channel = fallback_channel
        else:
            target = resolve_target(
                user_id=user_id,
                notification_type=notification_type,
                severity=severity,
                fallback_channel=fallback_channel,
            )
            if target.resolution_status == "PREFERENCE_DISABLED":
                return EnqueueResult(EnqueueStatus.PREFERENCE_DISABLED)
            if not target.source_id or not target.channel:
                return EnqueueResult(EnqueueStatus.SOURCE_NOT_FOUND)
            target_source_id = target.source_id
            target_channel = target.channel

        notification_id = self.repo.enqueue(
            user_id=user_id,
            source_id=target_source_id,
            channel=target_channel,
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
        if notification_id is None:
            return EnqueueResult(EnqueueStatus.DUPLICATE)
        return EnqueueResult(EnqueueStatus.CREATED, notification_id=notification_id)
