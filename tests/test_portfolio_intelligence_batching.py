import unittest
from dataclasses import dataclass

from jobs.portfolio_intelligence.batching import matched_batch_rows


@dataclass(frozen=True)
class Candidate:
    symbol: str


class BatchMatchingTests(unittest.TestCase):
    def test_matches_rows_by_position_and_symbol(self):
        batch = [Candidate("AAPL"), Candidate("MSFT")]
        rows = list(
            matched_batch_rows(
                batch,
                {
                    "results": [
                        {"symbol": "AAPL", "events": []},
                        {"symbol": "MSFT", "events": []},
                    ]
                },
                label="Test",
            )
        )
        self.assertEqual(rows[0][0].symbol, "AAPL")
        self.assertEqual(rows[1][1]["symbol"], "MSFT")

    def test_rejects_result_count_mismatch(self):
        with self.assertRaisesRegex(ValueError, "result count mismatch"):
            list(
                matched_batch_rows(
                    [Candidate("AAPL")],
                    {"results": []},
                    label="Test",
                )
            )

    def test_rejects_symbol_mismatch(self):
        with self.assertRaisesRegex(ValueError, "symbol mismatch"):
            list(
                matched_batch_rows(
                    [Candidate("AAPL")],
                    {"results": [{"symbol": "MSFT"}]},
                    label="Test",
                )
            )

    def test_rejects_non_list_results(self):
        with self.assertRaisesRegex(ValueError, "must be a list"):
            list(
                matched_batch_rows(
                    [Candidate("AAPL")],
                    {"results": {"symbol": "AAPL"}},
                    label="Test",
                )
            )


if __name__ == "__main__":
    unittest.main()
