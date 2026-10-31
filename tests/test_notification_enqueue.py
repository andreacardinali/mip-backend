import unittest
from unittest.mock import MagicMock

from alerts.rule_configuration import validate_rule_payload
from notifications.diagnostics import NotificationDiagnostics
from notifications.models import EnqueueResult, EnqueueStatus, NotificationTarget
from notifications.queue import NotificationQueue


class FakeRepository:
    def __init__(self, source_id="source", notification_id="notification"):
        self.source_id = source_id
        self.notification_id = notification_id

    def resolve_source_id(self, **kwargs):
        return self.source_id

    def enqueue(self, **kwargs):
        return self.notification_id


class EnqueueTests(unittest.TestCase):
    def test_pause_blocks_even_an_explicit_destination(self):
        queue = self._queue()
        queue.repo = MagicMock()
        queue.repo.resolve_target.return_value = NotificationTarget(None, None, "PAUSED")
        result = self._enqueue(queue, source_id="destination")
        self.assertEqual(result.status, EnqueueStatus.DISABLED)
        self.assertEqual(queue.repo.resolve_target.call_args.kwargs["source_id"], "destination")
        queue.repo.enqueue.assert_not_called()

    def test_destination_is_resolved_before_enqueue(self):
        queue = self._queue()
        queue.repo = MagicMock()
        queue.repo.resolve_target.return_value = NotificationTarget("owned", "TELEGRAM", "RESOLVED")
        queue.repo.enqueue.return_value = "notification"
        self.assertTrue(self._enqueue(queue, source_id="owned"))
        self.assertEqual(queue.repo.enqueue.call_args.kwargs["source_id"], "owned")

    def test_rule_destination_is_normalized_and_invalid_ids_are_rejected(self):
        payload = {
            "name": "Price alert",
            "conditions": {"source_id": "00000000-0000-0000-0000-000000000ABC"},
        }
        self.assertEqual(
            validate_rule_payload(payload)["conditions"]["source_id"],
            "00000000-0000-0000-0000-000000000abc",
        )
        for invalid in ("invalid", "", 1, True):
            with self.subTest(source_id=invalid), self.assertRaises(ValueError):
                validate_rule_payload({"name": "Alert", "conditions": {"source_id": invalid}})
        self.assertNotIn("source_id", validate_rule_payload({"name": "Alert"})["conditions"])

    def _queue(
        self,
        *,
        enabled=True,
        source_id="source",
        notification_id="notification",
    ):
        queue = object.__new__(NotificationQueue)
        queue.enabled = enabled
        queue.default_channel = "TELEGRAM"
        queue.repo = FakeRepository(source_id, notification_id)
        return queue

    def _enqueue(self, queue, **kwargs):
        return queue.enqueue(
            user_id="user",
            notification_key="key",
            notification_type="TEST",
            entity_id=None,
            symbol=None,
            event_key=None,
            severity="LOW",
            title="Title",
            message="Message",
            context={},
            **kwargs,
        )

    def test_created(self):
        result = self._enqueue(self._queue())
        self.assertEqual(result.status, EnqueueStatus.CREATED)
        self.assertTrue(result)

    def test_disabled(self):
        result = self._enqueue(self._queue(enabled=False))
        self.assertEqual(result.status, EnqueueStatus.DISABLED)
        self.assertFalse(result)

    def test_source_not_found(self):
        result = self._enqueue(self._queue(source_id=None))
        self.assertEqual(result.status, EnqueueStatus.SOURCE_NOT_FOUND)

    def test_duplicate(self):
        result = self._enqueue(self._queue(notification_id=None))
        self.assertEqual(result.status, EnqueueStatus.DUPLICATE)

    def test_diagnostics_counts_enqueue_outcomes(self):
        diagnostics = NotificationDiagnostics()
        outcomes = (
            EnqueueResult(EnqueueStatus.CREATED, "notification-1"),
            EnqueueResult(EnqueueStatus.DUPLICATE),
            EnqueueResult(EnqueueStatus.DISABLED),
            EnqueueResult(EnqueueStatus.SOURCE_NOT_FOUND),
        )

        self.assertTrue(diagnostics.record(outcomes[0]))
        for outcome in outcomes[1:]:
            self.assertFalse(diagnostics.record(outcome))

        self.assertEqual(
            diagnostics.to_dict(),
            {
                "notificationsCreated": 1,
                "notificationsDuplicate": 1,
                "notificationsDisabled": 1,
                "notificationsWithoutSource": 1,
            },
        )


if __name__ == "__main__":
    unittest.main()
