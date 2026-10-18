from dataclasses import dataclass
from typing import Any


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
