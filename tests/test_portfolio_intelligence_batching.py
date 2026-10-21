import unittest
from dataclasses import dataclass
from types import SimpleNamespace

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

    def test_fills_missing_results_with_empty_events(self):
        candidates = [
            SimpleNamespace(symbol="AAPL"),
            SimpleNamespace(symbol="MSFT"),
        ]

        rows = list(
            matched_batch_rows(
                candidates,
                {"results": []},
                label="Test",
            )
        )

        self.assertEqual(len(rows), len(candidates))

        for candidate, row in rows:
            self.assertEqual(row["symbol"], candidate.symbol)
            self.assertEqual(row["events"], [])

    def test_fills_only_missing_symbols(self):
        candidates = [
            SimpleNamespace(symbol="AAPL"),
            SimpleNamespace(symbol="MSFT"),
        ]
        first = candidates[0]

        rows = list(
            matched_batch_rows(
                candidates,
                {
                    "results": [
                        {
                            "symbol": first.symbol,
                            "events": [{"headline": "Evento"}],
                        }
                    ]
                },
                label="Test",
            )
        )

        rows_by_symbol = {candidate.symbol: row for candidate, row in rows}

        self.assertEqual(
            rows_by_symbol["AAPL"]["events"],
            [{"headline": "Evento"}],
        )
        self.assertEqual(
            rows_by_symbol["MSFT"]["events"],
            [],
        )


if __name__ == "__main__":
    unittest.main()
