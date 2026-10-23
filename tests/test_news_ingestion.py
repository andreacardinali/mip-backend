import unittest
from datetime import UTC, datetime
from unittest.mock import Mock, patch

from market_data.models import NewsArticle
from services.news_ingestion import _external_id, ingest_news


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


if __name__ == "__main__":
    unittest.main()
