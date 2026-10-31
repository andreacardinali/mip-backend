import json
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from services.news_enrichment import (
    EnrichmentStats,
    NewsScore,
    _parse_scores,
    _persist_scores,
    _score_batch,
)


class NewsEnrichmentTests(unittest.TestCase):
    def test_shared_score_writer_uses_sp_without_committing(self):
        db = Mock()
        db.execute.return_value.scalar_one.return_value = 1
        self.assertEqual(
            _persist_scores(db, [NewsScore("entity", 0.5, -0.2, 0.8)]), 1
        )
        statement, params = db.execute.call_args.args
        self.assertIn("public.sp_news_update_entity_scores", str(statement))
        self.assertEqual(
            json.loads(params["scores"]),
            [{"news_entity_id": "entity", "relevance": 0.5, "sentiment": -0.2, "impact": 0.8}],
        )
        db.commit.assert_not_called()
        db.rollback.assert_not_called()

    def test_scores_commit_independently_without_repeating_llm_call(self):
        events = []
        db = Mock()

        def execute(statement, params):
            if "public.sp_save_event" in str(statement):
                self.assertEqual(json.loads(params["envelope"])["stage"], "ENRICHED")
                return Mock(scalar_one=lambda: "lake-event-id")
            scores = json.loads(params["scores"])
            self.assertEqual(len(scores), 1)
            symbol = scores[0]["news_entity_id"]
            events.append(symbol)
            if symbol == "BAD":
                raise RuntimeError("write failed")
            return Mock(scalar_one=lambda: 1)

        db.execute.side_effect = execute
        db.commit.side_effect = lambda: events.append("commit")
        db.rollback.side_effect = lambda: events.append("rollback")
        provider = Mock()
        scores = [
            {"news_entity_id": symbol, "relevance": 0.5, "sentiment": 0, "impact": 0.5}
            for symbol in ("FIRST", "BAD", "THIRD")
        ]
        provider.generate_json.return_value = SimpleNamespace(data={"scores": scores})
        stats = EnrichmentStats()
        _score_batch(db, provider=provider, rows=scores, max_tokens=1000, stats=stats)
        self.assertEqual(stats.scored, 2)
        self.assertEqual(stats.failed_items, 1)
        self.assertEqual(stats.split_batches, 0)
        provider.generate_json.assert_called_once()
        self.assertEqual(
            events, ["FIRST", "commit", "BAD", "rollback", "THIRD", "commit"]
        )

    def test_parse_and_clamp_scores(self):
        result = _parse_scores(
            {
                "scores": [
                    {
                        "news_entity_id": "one",
                        "relevance": 1.2,
                        "sentiment": -2,
                        "impact": 0.7,
                    }
                ]
            },
            {"one"},
        )
        self.assertEqual(result[0].relevance, 1.0)
        self.assertEqual(result[0].sentiment, -1.0)
        self.assertEqual(result[0].impact, 0.7)

    def test_rejects_missing_id(self):
        with self.assertRaises(ValueError):
            _parse_scores({"scores": []}, {"one"})

    def test_rejects_duplicate_id(self):
        item = {
            "news_entity_id": "one",
            "relevance": 0.5,
            "sentiment": 0.0,
            "impact": 0.5,
        }
        with self.assertRaises(ValueError):
            _parse_scores({"scores": [item, item]}, {"one"})


if __name__ == "__main__":
    unittest.main()
