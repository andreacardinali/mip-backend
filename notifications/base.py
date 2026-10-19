from typing import Protocol

from notifications.models import NotificationDelivery, NotificationSendResult


class NotificationPlugin(Protocol):
    channel: str

    def send(
        self,
        delivery: NotificationDelivery,
    ) -> NotificationSendResult: ...
