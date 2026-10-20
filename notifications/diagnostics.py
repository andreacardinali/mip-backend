from collections import Counter
from typing import Any

from notifications.models import EnqueueResult, EnqueueStatus


class NotificationDiagnostics:
    def __init__(self) -> None:
        self._counts: Counter[EnqueueStatus] = Counter()

    def record(self, result: EnqueueResult) -> bool:
        self._counts[result.status] += 1
        return result.created

    def to_dict(self) -> dict[str, Any]:
        return {
            "notificationsCreated": self._counts[EnqueueStatus.CREATED],
            "notificationsDuplicate": self._counts[EnqueueStatus.DUPLICATE],
            "notificationsDisabled": self._counts[EnqueueStatus.DISABLED],
            "notificationsWithoutSource": self._counts[EnqueueStatus.SOURCE_NOT_FOUND],
        }
