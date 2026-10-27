from __future__ import annotations

from notifications.diagnostics import NotificationDiagnostics


class IncrementalNotificationWriter:
    """Enqueue accepted results immediately with in-run and database deduplication."""

    def __init__(self, queue) -> None:
        self.queue = queue
        self.diagnostics = NotificationDiagnostics()
        self._seen_keys: set[str] = set()
        self.duplicate_count = 0

    def enqueue(self, *, notification_key: str, **values) -> bool:
        if notification_key in self._seen_keys:
            self.duplicate_count += 1
            return False
        self._seen_keys.add(notification_key)
        result = self.queue.enqueue(
            notification_key=notification_key,
            **values,
        )
        return self.diagnostics.record(result)

    def to_dict(self) -> dict:
        return {
            "inRunDuplicates": self.duplicate_count,
            **self.diagnostics.to_dict(),
        }
