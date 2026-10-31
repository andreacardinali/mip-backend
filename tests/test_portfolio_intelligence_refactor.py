import json
import unittest
from contextlib import ExitStack
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import Mock, patch

from job_runtime.collections import batched, deduplicate_by
from job_runtime.models import JobStatus
from jobs.portfolio_intelligence.branches.breaking_catalyst import (
    BreakingCatalystBranch,
)
from jobs.portfolio_intelligence.branches.daily_mover import DailyMoverBranch
from jobs.portfolio_intelligence.branches.upcoming_catalyst import (
    UpcomingCatalystBranch,
)
from jobs.portfolio_intelligence.delivery import CollectionEventPublisher
from jobs.portfolio_intelligence.incremental import IncrementalNotificationWriter
from jobs.portfolio_intelligence.messages import (
    format_breaking_catalyst_message,
    format_daily_mover_message,
)
from jobs.portfolio_intelligence.models import BranchResult, EntityCandidate
from jobs.portfolio_intelligence.parsing import (
    parse_breaking_event,
    parse_upcoming_event,
)
from jobs.portfolio_intelligence.repositories.catalysts import (
    save_intelligence_event,
    upsert_catalyst,
)
from jobs.portfolio_intelligence.repositories.entities import (
    load_acquisition_candidates,
    load_candidates,
)
from jobs.portfolio_intelligence.scoring import (
    breaking_catalyst_severity,
    upcoming_catalyst_severity,
)
from notifications.models import EnqueueResult, EnqueueStatus


class RefactorTests(unittest.TestCase):
    def test_shared_intelligence_writer_preserves_payload_evidence_and_commit(self):
        db = Mock()
        payload = {"headline": "News", "eventConfidence": 85}
        evidence = {"sources": [{"url": "source"}]}
        save_intelligence_event(
            db,
            branch="BREAKING_CATALYST",
            event_key="event",
            entity_id="entity",
            instrument_id="instrument",
            category="NEWS",
            payload=payload,
            evidence=evidence,
        )
        statement, params = db.execute.call_args.args
        self.assertIn("public.sp_save_event", str(statement))
        envelope = json.loads(params["envelope"])
        self.assertEqual(envelope["payload"], payload)
        self.assertEqual(envelope["evidence"], evidence)
        self.assertEqual(envelope["event_key"], "event")
        self.assertNotIn("user_id", envelope)
        db.commit.assert_called_once_with()
        db.rollback.assert_not_called()

    def test_shared_intelligence_writer_rolls_back_execute_or_commit_failure(self):
        for operation in ("execute", "commit"):
            with self.subTest(operation=operation):
                db = Mock()
                getattr(db, operation).side_effect = RuntimeError("write failed")
                with self.assertRaisesRegex(RuntimeError, "write failed"):
                    save_intelligence_event(
                        db,
                        branch="DAILY_MOVER",
                        event_key="event",
                        entity_id="entity",
                        instrument_id="instrument",
                        category="DAILY_MOVER",
                        payload={},
                        evidence={},
                    )
                db.rollback.assert_called_once_with()

    def test_intelligence_catalyst_uses_shared_writer_and_preserves_date_only(self):
        db = Mock()
        db.execute.return_value.scalar_one.return_value = "catalyst-id"
        payload = {"eventConfidence": 85}
        result = upsert_catalyst(
            db,
            entity_id="entity",
            source_symbol="TEST",
            event_subtype="EARNINGS",
            event_date=date(2026, 7, 1),
            status="SCHEDULED",
            importance="HIGH",
            title="Earnings",
            source="research",
            payload=payload,
        )
        self.assertEqual(result, "catalyst-id")
        statement, params = db.execute.call_args.args
        self.assertIn("public.sp_save_event", str(statement))
        envelope = json.loads(params["envelope"])
        self.assertEqual(envelope["subjects"][0]["symbol"], "TEST")
        self.assertEqual(envelope["payload"]["subtype"], "EARNINGS")
        self.assertIsNone(envelope["occurred_at"])
        self.assertEqual(envelope["event_day"], "2026-07-01")
        self.assertEqual(
            envelope["payload"]["details"],
            {"eventConfidence": 85, "date": "2026-07-01"},
        )
        self.assertEqual(payload, {"eventConfidence": 85})
        db.commit.assert_not_called()
        db.rollback.assert_not_called()

    def test_catalyst_payloads_persist_normalized_date_and_confidence(self):
        for parser in (parse_upcoming_event, parse_breaking_event):
            for confidence in (True, "85", 85.5):
                with self.subTest(parser=parser.__name__, confidence=confidence):
                    payload = {
                        "eventType": " EARNINGS ",
                        "eventDate": "2026-W44-6",
                        "eventConfidence": confidence,
                        "eventFingerprint": "event",
                    }
                    event = parser(payload)
                    self.assertEqual(event.payload["eventType"], "EARNINGS")
                    self.assertEqual(event.payload["eventDate"], "2026-10-31")
                    self.assertIs(type(event.payload["eventConfidence"]), int)
                    self.assertEqual(event.payload["eventConfidence"], event.confidence)
                    self.assertEqual(payload["eventDate"], "2026-W44-6")

    def test_delivery_failure_does_not_block_other_collections(self):
        source = EntityCandidate(
            "", "entity", "instrument", "AAPL", "Apple", None, "USD", None
        )
        first = replace(
            source,
            user_id="alice",
            collection_type="WATCHLIST",
            collection_id="first",
            enabled_categories=["NEWS"],
        )
        second = replace(first, collection_id="second")
        context = Mock()
        context.notification_queue.enqueue.side_effect = [
            RuntimeError("write failed"),
            EnqueueResult(EnqueueStatus.CREATED, notification_id="created"),
        ]
        with (
            patch("jobs.portfolio_intelligence.delivery.save_intelligence_event"),
            patch(
                "jobs.portfolio_intelligence.delivery.load_candidates",
                return_value=[first, second],
            ),
        ):
            publisher = CollectionEventPublisher(context, "BREAKING_CATALYST")
            count = publisher.publish(
                source,
                event_key="event",
                category="NEWS",
                payload={"headline": "News"},
                evidence={"sources": []},
                decorate=lambda recipient: {"severity": "LOW"},
                format_message=lambda item: "message",
            )
        self.assertEqual(count, 1)
        self.assertEqual(publisher.accepted_count, 1)
        self.assertEqual(len(publisher.errors), 1)
        context.db.rollback.assert_called_once()
        self.assertEqual(context.notification_queue.enqueue.call_count, 2)

    def test_delivery_filter_does_not_discard_shared_event(self):
        source = EntityCandidate(
            "", "entity", "instrument", "AAPL", "Apple", None, "USD", None
        )
        context = Mock()
        with (
            patch(
                "jobs.portfolio_intelligence.delivery.save_intelligence_event"
            ) as save,
            patch("jobs.portfolio_intelligence.delivery.load_candidates") as recipients,
        ):
            publisher = CollectionEventPublisher(context, "BREAKING_CATALYST")
            count = publisher.publish(
                source,
                event_key="event",
                category="NEWS",
                payload={"eventConfidence": 30},
                evidence={"sources": []},
                decorate=lambda recipient: {},
                format_message=lambda item: "message",
                deliver=False,
            )
        save.assert_called_once()
        recipients.assert_not_called()
        self.assertEqual(count, 0)
        self.assertEqual(publisher.accepted_count, 1)

    def test_shared_event_is_saved_before_subscription_lookup(self):
        source = EntityCandidate(
            "", "entity", "instrument", "AAPL", "Apple", None, "USD", None
        )
        context = Mock()
        order = []
        with (
            patch(
                "jobs.portfolio_intelligence.delivery.save_intelligence_event",
                side_effect=lambda *args, **kwargs: order.append("save"),
            ) as save,
            patch(
                "jobs.portfolio_intelligence.delivery.load_candidates",
                side_effect=lambda *args: order.append("subscriptions") or [],
            ),
        ):
            count = CollectionEventPublisher(context, "BREAKING_CATALYST").publish(
                source,
                event_key="event",
                category="NEWS",
                payload={"headline": "News"},
                evidence={"sources": []},
                decorate=lambda recipient: {"severity": "LOW"},
                format_message=lambda item: "message",
            )
        self.assertEqual(order, ["save", "subscriptions"])
        self.assertEqual(count, 0)
        self.assertNotIn("user_id", save.call_args.kwargs["payload"])
        context.notification_queue.enqueue.assert_not_called()

    def test_acquisition_uses_shared_coverage_without_subscriptions(self):
        database = Mock()
        database.execute.return_value.scalars.return_value.all.return_value = [
            {
                "entity_id": "entity",
                "instrument_id": "instrument",
                "symbol": "AAPL",
                "name": "Apple",
                "exchange": None,
                "currency_code": "USD",
                "instrument_type": None,
                "related_symbols": [],
            }
        ]
        candidate = load_acquisition_candidates(database, "BREAKING_CATALYST")[0]
        self.assertIn(
            "sp_get_intelligence_coverage", str(database.execute.call_args.args[0])
        )
        self.assertEqual(candidate.user_id, "")
        self.assertIsNone(candidate.collection_id)
        self.assertEqual(candidate.enabled_categories, [])
        research = candidate.research_context()
        self.assertNotIn("user_id", research)
        self.assertNotIn("allocation_pct", research)
        self.assertNotIn("collection_id", research)

    def test_repository_preserves_collection_and_unknown_exposure(self):
        database = Mock()
        database.execute.return_value.scalars.return_value.all.return_value = [
            {
                "user_id": "alice",
                "entity_id": "entity",
                "instrument_id": "instrument",
                "symbol": "AAPL",
                "name": "Apple",
                "exchange": None,
                "currency_code": "USD",
                "instrument_type": None,
                "quantity": None,
                "market_value": None,
                "allocation_pct": None,
                "in_portfolio": False,
                "in_watchlist": True,
                "related_symbols": [],
                "collection_type": "WATCHLIST",
                "collection_id": "second",
                "collection_name": "Osservati",
                "enabled_categories": ["NEWS"],
            }
        ]
        candidate = load_candidates(database, "BREAKING_CATALYST")[0]
        self.assertEqual(candidate.collection_id, "second")
        self.assertEqual(candidate.enabled_categories, ["NEWS"])
        self.assertIsNone(candidate.notification_context()["allocationPct"])
        self.assertEqual(
            database.execute.call_args.args[1], {"branch": "BREAKING_CATALYST"}
        )

    def test_collection_exposure_is_not_reported_as_zero_when_unavailable(self):
        candidate = EntityCandidate(
            user_id="alice",
            entity_id="entity",
            instrument_id="instrument",
            symbol="AAPL",
            name="Apple",
            exchange=None,
            currency="USD",
            instrument_type=None,
            collection_type="PORTFOLIO",
            collection_id="first",
            collection_name="Investimenti",
        )
        context = candidate.notification_context()
        self.assertEqual(context["collectionId"], "first")
        self.assertIsNone(context["allocationPct"])
        self.assertIsNone(context["positionMarketValue"])
        self.assertFalse(context["exposureAvailable"])

    def test_incremental_writer_deduplicates_per_user_and_collection(self):
        queue = Mock()
        queue.enqueue.return_value = EnqueueResult(
            EnqueueStatus.CREATED, notification_id="created"
        )
        writer = IncrementalNotificationWriter(queue)
        first = {"collectionType": "PORTFOLIO", "collectionId": "first"}
        second = {"collectionType": "WATCHLIST", "collectionId": "second"}
        for user_id, context in [("alice", first), ("bob", first), ("alice", second)]:
            self.assertTrue(
                writer.enqueue(
                    notification_key="EVENT", user_id=user_id, context=context
                )
            )
        self.assertFalse(
            writer.enqueue(notification_key="EVENT", user_id="alice", context=first)
        )
        self.assertEqual(queue.enqueue.call_count, 3)
        self.assertEqual(
            queue.enqueue.call_args.kwargs["notification_key"],
            "EVENT|WATCHLIST|second",
        )

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


class CatalystResearchHistoryTests(unittest.TestCase):
    def test_history_uses_shared_routine_and_excludes_recipient_metadata(self):
        from jobs.portfolio_intelligence.repositories.catalysts import recent_catalysts

        db = Mock()
        db.execute.return_value.mappings.return_value.all.return_value = [
            {
                "catalyst_id": "shared-event",
                "entity_id": "entity",
                "catalyst_type": "EARNINGS",
                "catalyst_date": date.today(),
                "status": "EXPECTED",
                "importance": 80,
                "title": "Earnings",
                "source": "market",
                "raw_payload": {"user_id": "owner", "allocationPct": 25},
                "symbols": [{"symbol": "AAPL", "collectionId": "private"}],
            }
        ]
        history = recent_catalysts(db, ["entity", "entity"])
        statement, params = db.execute.call_args.args
        self.assertIn("sp_get_recent_intelligence_catalysts", str(statement))
        self.assertEqual(params["ids"], ["entity"])
        self.assertEqual(history["entity"][0]["catalystId"], "shared-event")
        self.assertNotIn("payload", history["entity"][0])
        self.assertNotIn("private", json.dumps(history))
        self.assertNotIn("allocationPct", json.dumps(history))

    def test_empty_coverage_does_not_query_history(self):
        from jobs.portfolio_intelligence.repositories.catalysts import recent_catalysts

        db = Mock()
        self.assertEqual(recent_catalysts(db, []), {})
        db.execute.assert_not_called()


class SharedBranchTests(unittest.TestCase):
    def setUp(self):
        self.source = EntityCandidate(
            "owner",
            "entity",
            "instrument",
            "AAPL",
            "Apple",
            None,
            "USD",
            "STOCK",
            quantity=10,
            market_value=1000,
            allocation_pct=25,
            collection_type="PORTFOLIO",
            collection_id="private",
            collection_name="Private",
            exposure_available=True,
        )
        self.context = Mock()
        self.context.config = SimpleNamespace(
            llm_request_max_candidates=10,
            breaking_lookback_hours=24,
            upcoming_window_days=7,
            history_range="1mo",
            history_interval="1d",
            min_history_rows=1,
            daily_move_min_pct=3,
        )
        self.context.prompts.get.return_value.render.return_value = "prompt"
        self.context.prompts.get.return_value.fingerprint = "fingerprint"
        self.context.research_llm.generate_json.return_value = SimpleNamespace(
            data={"results": [{"symbol": "AAPL", "events": []}]},
            sources=[],
        )
        self.context.validation_llm.generate_json.return_value = SimpleNamespace(
            data={"symbol": "AAPL", "causeFound": False, "headline": "Move"},
        )
        self.context.market_data.get_ohlcv.return_value = [1, 2]
        self.context.notification_queue.enqueue.return_value = EnqueueResult(
            EnqueueStatus.CREATED,
            notification_id="created",
        )
        self.collector = Mock()
        self.collector.common_economic_events.return_value = []
        self.collector.diagnostics.to_dict.return_value = {}
        for method in ("for_daily_mover", "for_upcoming", "for_breaking"):
            getattr(self.collector, method).return_value.to_dict.return_value = {
                "news": []
            }

    def execute(self, branch, recipients=(), save_effect=None):
        module = type(branch).__module__
        with ExitStack() as stack:
            stack.enter_context(
                patch(
                    f"{module}.load_acquisition_candidates", return_value=[self.source]
                )
            )
            stack.enter_context(
                patch(f"{module}.MarketEvidenceCollector", return_value=self.collector)
            )
            self.save = stack.enter_context(
                patch(
                    "jobs.portfolio_intelligence.delivery.save_intelligence_event",
                    side_effect=save_effect,
                )
            )
            self.recipients = stack.enter_context(
                patch(
                    "jobs.portfolio_intelligence.delivery.load_candidates",
                    return_value=list(recipients),
                )
            )
            if isinstance(branch, UpcomingCatalystBranch):
                stack.enter_context(
                    patch(f"{module}.recent_catalysts", return_value={})
                )
            if isinstance(branch, DailyMoverBranch):
                snapshot = Mock(daily_change_pct=6)
                snapshot.to_dict.return_value = {
                    "dailyChangePct": 6,
                    "observedDate": 1800000000,
                }
                stack.enter_context(
                    patch(f"{module}.analyze_price_bars", return_value=snapshot)
                )
            return branch.execute(self.context)

    def event(self, confidence=95, event_date=None, fingerprint="event"):
        return {
            "eventType": "NEWS",
            "eventDate": (event_date or date.today()).isoformat(),
            "eventConfidence": confidence,
            "eventFingerprint": fingerprint,
            "headline": "News",
        }

    def set_events(self, events):
        self.context.research_llm.generate_json.return_value.data["results"][0][
            "events"
        ] = events

    def assert_shared_payload(self, payload):
        forbidden = {
            "user_id",
            "quantity",
            "market_value",
            "allocation_pct",
            "enabled_categories",
            "collection_id",
            "collection_name",
            "collection_type",
            "in_portfolio",
            "in_watchlist",
            "collectionId",
            "collectionName",
            "allocationPct",
            "positionMarketValue",
            "portfolioImpactPct",
            "severity",
            "severityScore",
            "exposureAvailable",
        }
        self.assertFalse(forbidden.intersection(payload))

    def test_daily_research_is_shared_and_exposure_is_projected_per_portfolio(self):
        first = replace(
            self.source,
            user_id="alice",
            collection_id="first",
            allocation_pct=1,
            enabled_categories=["DAILY_MOVER"],
        )
        second = replace(
            first, user_id="bob", collection_id="second", allocation_pct=30
        )
        other_listing = replace(second, instrument_id="other-instrument")
        result = self.execute(DailyMoverBranch(), [first, second, other_listing])
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.notification_count, 2)
        self.assert_shared_payload(self.save.call_args.kwargs["payload"])
        observed = json.loads(
            self.context.prompts.get.return_value.render.call_args.kwargs["observed"]
        )
        self.assert_shared_payload(observed)
        items = [
            call.kwargs["context"]
            for call in self.context.notification_queue.enqueue.call_args_list
        ]
        self.assertEqual([item["severity"] for item in items], ["MEDIUM", "HIGH"])
        self.assertEqual([item["portfolioImpactPct"] for item in items], [0.06, 1.8])
        self.assertTrue(
            self.save.call_args.kwargs["event_key"].startswith("instrument|")
        )

    def test_breaking_low_confidence_is_saved_without_delivery(self):
        self.set_events([self.event(confidence=30)])
        result = self.execute(BreakingCatalystBranch())
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.notification_count, 0)
        self.recipients.assert_not_called()
        self.assertEqual(self.save.call_args.kwargs["category"], "NEWS")
        self.assert_shared_payload(self.save.call_args.kwargs["payload"])
        candidates = json.loads(
            self.context.prompts.get.return_value.render.call_args.kwargs["candidates"]
        )
        self.assert_shared_payload(candidates[0])

    def test_breaking_news_is_saved_without_subscribers(self):
        self.set_events([self.event()])
        result = self.execute(BreakingCatalystBranch())
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.notification_count, 0)
        self.recipients.assert_called_once()
        self.context.notification_queue.enqueue.assert_not_called()

    def test_upcoming_outside_window_is_saved_without_delivery(self):
        self.set_events([self.event(event_date=date.today() + timedelta(days=30))])
        result = self.execute(UpcomingCatalystBranch())
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.notification_count, 0)
        self.recipients.assert_not_called()
        self.assert_shared_payload(self.save.call_args.kwargs["payload"])
        candidates = json.loads(
            self.context.prompts.get.return_value.render.call_args.kwargs["candidates"]
        )
        self.assert_shared_payload(candidates[0])

    def test_persistence_failure_does_not_block_the_next_breaking_event(self):
        self.set_events(
            [self.event(fingerprint="first"), self.event(fingerprint="second")]
        )
        result = self.execute(
            BreakingCatalystBranch(), save_effect=[RuntimeError("save failed"), None]
        )
        self.assertEqual(result.status, JobStatus.PARTIAL)
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(self.save.call_count, 2)
        self.assertEqual(len(result.errors), 1)


if __name__ == "__main__":
    unittest.main()
