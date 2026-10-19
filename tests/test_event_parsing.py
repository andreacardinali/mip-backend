import unittest

from jobs.portfolio_intelligence.parsing import (
    PayloadValidationError,
    parse_breaking_event,
    parse_daily_validation,
    parse_upcoming_event,
)


class EventParsingTests(unittest.TestCase):
    def test_upcoming(self):
        event = parse_upcoming_event(
            {
                "eventType": "EARNINGS",
                "eventDate": "2026-10-20T00:00:00Z",
                "eventConfidence": 80,
            }
        )
        self.assertEqual(event.event_date.isoformat(), "2026-10-20")

    def test_breaking_requires_fingerprint(self):
        with self.assertRaises(PayloadValidationError):
            parse_breaking_event(
                {
                    "eventType": "NEWS",
                    "eventDate": "2026-10-20",
                    "eventConfidence": 80,
                }
            )

    def test_daily_normalizes_no_cause(self):
        result = parse_daily_validation(
            {
                "symbol": "AAPL",
                "causeFound": False,
                "sourceIndicesUsed": [1],
            },
            expected_symbol="AAPL",
        )
        self.assertEqual(result.source_indices, [])
        self.assertEqual(result.payload["causeConfidence"], 0)


if __name__ == "__main__":
    unittest.main()
