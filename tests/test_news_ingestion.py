import json
import unittest
from contextlib import nullcontext
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from market_data.models import NewsArticle
from services.event_persistence import save_news_article
from services.news_ingestion import _external_id, _published_at, ingest_news


class R:
    def __init__(self, v):
        self.v = v

    def mappings(self):
        return self

    def all(self):
        return self.v

    def scalar_one(self):
        return self.v


class Tests(unittest.TestCase):
    def test_shared_news_writer_uses_sp_and_leaves_transaction_to_caller(self):
        payload = {"headline": "News", "external_id": "stable-id"}
        expected = {"inserted": True, "associations_inserted": 1}
        for result in (expected, json.dumps(expected)):
            with self.subTest(result=result):
                db = Mock()
                db.execute.return_value = R(result)
                self.assertEqual(
                    save_news_article(db, entity_id="entity", payload=payload),
                    expected,
                )
                statement, params = db.execute.call_args.args
                self.assertIn("public.sp_news_upsert_article", str(statement))
                self.assertEqual(params["entity_id"], "entity")
                self.assertEqual(json.loads(params["payload"]), payload)
                db.commit.assert_not_called()
                db.rollback.assert_not_called()

    def test_shared_news_writer_propagates_database_failure(self):
        db = Mock()
        db.execute.side_effect = RuntimeError("write failed")
        with self.assertRaisesRegex(RuntimeError, "write failed"):
            save_news_article(db, entity_id="entity", payload={})
        db.commit.assert_not_called()
        db.rollback.assert_not_called()

    def test_naive_news_timestamp_is_rejected(self):
        article = NewsArticle(headline="x", published_at=datetime(2026, 7, 1))
        with self.assertRaises(ValueError):
            _published_at(article)

    def test_stable_id(self):
        a = NewsArticle(
            headline="x",
            published_at=datetime(2026, 10, 23, tzinfo=UTC),
            url="u",
            source="E",
        )
        self.assertEqual(_external_id(a), _external_id(a))

    @patch("services.news_ingestion.settings")
    def test_ingest(self, s):
        s.get.side_effect = lambda n, d=None: d
        db = Mock()
        db.begin_nested.return_value = nullcontext()
        db.execute.side_effect = [
            R(
                [
                    {
                        "entity_id": "11111111-1111-1111-1111-111111111111",
                        "symbol": "MSFT",
                        "instrument_type": "STOCK",
                    }
                ]
            ),
            R("lake-event-id"),
            R({"inserted": True, "associations_inserted": 1}),
        ]
        md = Mock()
        md.get_news.return_value = [
            NewsArticle(
                headline="n",
                published_at=datetime.now(UTC),
                external_id="1",
                source="E",
            )
        ]
        result = ingest_news(
            db,
            market_data_service=md,
            target_limit=10,
            news_limit=5,
            lookback_hours=24,
            include_all_instruments=False,
        )
        self.assertEqual(result["inserted"], 1)
        self.assertEqual(result["associations_inserted"], 1)
        self.assertEqual(result["persistence_failed"], 0)
        db.begin_nested.assert_called_once_with()
        self.assertEqual(db.commit.call_count, 2)

    @patch("services.news_ingestion._persist_article")
    @patch("services.news_ingestion._load_targets")
    def test_commits_articles_before_next_provider_and_continues_after_write_failure(
        self, load_targets, persist_article
    ):
        load_targets.return_value = [
            {"entity_id": symbol, "symbol": symbol}
            for symbol in ("FIRST", "SECOND", "THIRD")
        ]
        events = []
        db = Mock()
        db.begin_nested.return_value = nullcontext()
        db.commit.side_effect = lambda: events.append("commit")
        db.rollback.side_effect = lambda: events.append("rollback")
        persist_article.side_effect = [
            {"inserted": True},
            RuntimeError("write failed"),
            {"inserted": True},
        ]

        def fetch(reference, **kwargs):
            self.assertIn(events[-1], {"commit", "rollback"})
            events.append(reference.symbol)
            return [
                NewsArticle(
                    headline="news",
                    published_at=datetime.now(UTC),
                    external_id=reference.symbol,
                )
            ]

        md = Mock()
        md.get_news.side_effect = fetch
        result = ingest_news(db, market_data_service=md)

        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["persistence_failed"], 1)
        self.assertEqual(
            events,
            ["commit", "FIRST", "commit", "SECOND", "rollback", "THIRD", "commit"],
        )


if __name__ == "__main__":
    unittest.main()
