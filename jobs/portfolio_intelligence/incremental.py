from __future__ import annotations

from notifications.diagnostics import NotificationDiagnostics


class IncrementalNotificationWriter:
    """Enqueue accepted results immediately with in-run and database deduplication."""

    def __init__(self, queue, *, db=None) -> None:
        self.queue = queue
        self.db = db
        self.diagnostics = NotificationDiagnostics()
        self._seen_keys: set[tuple[str | None, str]] = set()
        self.duplicate_count = 0

    def enqueue(self, *, notification_key: str, **values) -> bool:
        context = values.get("context") or {}
        collection_type = context.get("collectionType")
        collection_id = context.get("collectionId")
        if collection_type and collection_id:
            notification_key = f"{notification_key}|{collection_type}|{collection_id}"
        seen_key = (values.get("user_id"), notification_key)
        if seen_key in self._seen_keys:
            self.duplicate_count += 1
            return False
        try:
            result = self.queue.enqueue(
                notification_key=notification_key,
                **values,
            )
            if self.db is not None:
                self.db.commit()
        except Exception:
            if self.db is not None:
                self.db.rollback()
            raise
        self._seen_keys.add(seen_key)
        return self.diagnostics.record(result)

    def to_dict(self) -> dict:
        return {
            "inRunDuplicates": self.duplicate_count,
            **self.diagnostics.to_dict(),
        }
