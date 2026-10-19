from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class EnqueueStatus(StrEnum):
    CREATED = "CREATED"
    DISABLED = "DISABLED"
    SOURCE_NOT_FOUND = "SOURCE_NOT_FOUND"
    DUPLICATE = "DUPLICATE"


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    status: EnqueueStatus
    notification_id: str | None = None

    @property
    def created(self) -> bool:
        return self.status == EnqueueStatus.CREATED

    def __bool__(self) -> bool:
        """Keep existing boolean callers compatible during migration."""
        return self.created


@dataclass(frozen=True, slots=True)
class NotificationDelivery:
    notification_id: str
    user_id: str
    source_id: str
    channel: str
    notification_key: str
    notification_type: str
    severity: str
    title: str
    message: str
    context: dict[str, Any]
    source_external_id: str | None
    source_config: dict[str, Any]
    attempt_count: int


@dataclass(frozen=True, slots=True)
class NotificationSendResult:
    provider_message_id: str | None
    raw: dict[str, Any]
