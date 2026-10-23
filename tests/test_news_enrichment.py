import unittest

from services.news_enrichment import _parse_scores


class NewsEnrichmentTests(unittest.TestCase):
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
