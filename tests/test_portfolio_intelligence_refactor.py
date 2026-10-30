import unittest
from unittest.mock import Mock

from job_runtime.collections import batched, deduplicate_by
from job_runtime.models import JobStatus
from jobs.portfolio_intelligence.incremental import IncrementalNotificationWriter
from jobs.portfolio_intelligence.messages import (
    format_breaking_catalyst_message,
    format_daily_mover_message,
)
from jobs.portfolio_intelligence.models import BranchResult
from jobs.portfolio_intelligence.scoring import (
    breaking_catalyst_severity,
    upcoming_catalyst_severity,
)
from notifications.models import EnqueueResult, EnqueueStatus


class RefactorTests(unittest.TestCase):
    def test_incremental_writer_commits_and_retries_failed_keys(self):
        db = Mock()
        queue = Mock()
        queue.enqueue.side_effect = [
            EnqueueResult(EnqueueStatus.CREATED, notification_id="first"),
            RuntimeError("write failed"),
            EnqueueResult(EnqueueStatus.CREATED, notification_id="second"),
        ]
        writer = IncrementalNotificationWriter(queue, db=db)
        self.assertTrue(writer.enqueue(notification_key="FIRST"))
        db.commit.assert_called_once()
        with self.assertRaisesRegex(RuntimeError, "write failed"):
            writer.enqueue(notification_key="SECOND")
        db.rollback.assert_called_once()
        self.assertTrue(writer.enqueue(notification_key="SECOND"))
        self.assertFalse(writer.enqueue(notification_key="FIRST"))
        self.assertEqual(db.commit.call_count, 2)

    def test_batched_validates_size(self):
        with self.assertRaises(ValueError):
            list(batched([1, 2], 0))
        self.assertEqual(list(batched([1, 2, 3], 2)), [[1, 2], [3]])

    def test_deduplicate_keeps_last(self):
        items = [
            {"id": 1, "v": "a"},
            {"id": 2, "v": "b"},
            {"id": 1, "v": "c"},
        ]
        self.assertEqual(
            deduplicate_by(items, key=lambda x: x["id"]),
            [items[1], items[2]],
        )

    def test_branch_result_becomes_partial(self):
        result = BranchResult(branch="TEST")
        result.add_error("bad row")
        self.assertEqual(result.status, JobStatus.PARTIAL)
        self.assertEqual(result.to_dict()["status"], "PARTIAL")

    def test_scoring_preserves_policies(self):
        self.assertEqual(breaking_catalyst_severity(90, 20), "CRITICAL")
        self.assertEqual(upcoming_catalyst_severity(1, 20), ("CRITICAL", 6))

    def test_messages(self):
        daily = format_daily_mover_message(
            {
                "symbol": "AAPL",
                "dailyChangePct": 3.5,
                "severity": "MEDIUM",
                "headline": "Move",
                "explanation": "Why",
                "recommendedCheck": "Check",
            }
        )
        self.assertIn("+3.50%", daily)
        breaking = format_breaking_catalyst_message(
            {
                "symbol": "AAPL",
                "eventType": "NEWS",
                "severity": "HIGH",
                "headline": "Headline",
                "eventSummary": "Summary",
                "materialityReason": "Reason",
            }
        )
        self.assertIn("BREAKING CATALYST", breaking)


if __name__ == "__main__":
    unittest.main()
