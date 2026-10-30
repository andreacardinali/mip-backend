import unittest
from contextlib import nullcontext
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from market_data.models import NewsArticle
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
            {"inserted": True}, RuntimeError("write failed"), {"inserted": True}
        ]

        def fetch(reference, **kwargs):
            self.assertIn(events[-1], {"commit", "rollback"})
            events.append(reference.symbol)
            return [NewsArticle(
                headline="news", published_at=datetime.now(UTC), external_id=reference.symbol
            )]

        md = Mock()
        md.get_news.side_effect = fetch
        result = ingest_news(db, market_data_service=md)

        self.assertEqual(result["inserted"], 2)
        self.assertEqual(result["persistence_failed"], 1)
        self.assertEqual(events, ["commit", "FIRST", "commit", "SECOND", "rollback", "THIRD", "commit"])


if __name__ == "__main__":
    unittest.main()
