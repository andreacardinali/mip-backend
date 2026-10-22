import unittest
from dataclasses import dataclass

from jobs.portfolio_intelligence.batching import (
    matched_batch_rows,
)


@dataclass(frozen=True)
class Candidate:
    symbol: str
    candidate_id: str = ""


class BatchMatchingTests(unittest.TestCase):
    def test_matches_rows_by_position_and_symbol(
        self,
    ):
        batch = [
            Candidate(
                symbol="AAPL",
                candidate_id="candidate-1",
            ),
            Candidate(
                symbol="MSFT",
                candidate_id="candidate-2",
            ),
        ]

        rows = list(
            matched_batch_rows(
                batch,
                {
                    "results": [
                        {
                            "symbol": "AAPL",
                            "events": [],
                        },
                        {
                            "symbol": "MSFT",
                            "events": [],
                        },
                    ],
                },
                label="Test",
            )
        )

        self.assertEqual(len(rows), 2)

        self.assertIs(rows[0][0], batch[0])
        self.assertIs(rows[1][0], batch[1])

        self.assertEqual(
            rows[0][1]["symbol"],
            "AAPL",
        )
        self.assertEqual(
            rows[1][1]["symbol"],
            "MSFT",
        )

    def test_accepts_duplicate_symbols_by_position(
        self,
    ):
        batch = [
            Candidate(
                symbol="DELL",
                candidate_id="portfolio",
            ),
            Candidate(
                symbol="DELL",
                candidate_id="watchlist",
            ),
        ]

        response = {
            "results": [
                {
                    "symbol": "DELL",
                    "events": [
                        {
                            "headline": ("Portfolio event"),
                        },
                    ],
                },
                {
                    "symbol": "DELL",
                    "events": [
                        {
                            "headline": ("Watchlist event"),
                        },
                    ],
                },
            ],
        }

        rows = list(
            matched_batch_rows(
                batch,
                response,
                label="Breaking",
            )
        )

        self.assertEqual(len(rows), 2)

        self.assertEqual(
            rows[0][0].candidate_id,
            "portfolio",
        )
        self.assertEqual(
            rows[1][0].candidate_id,
            "watchlist",
        )

        self.assertEqual(
            rows[0][1]["events"][0]["headline"],
            "Portfolio event",
        )
        self.assertEqual(
            rows[1][1]["events"][0]["headline"],
            "Watchlist event",
        )

    def test_rejects_symbol_mismatch(
        self,
    ):
        with self.assertRaisesRegex(
            ValueError,
            "symbol mismatch at index 0",
        ):
            list(
                matched_batch_rows(
                    [
                        Candidate(
                            symbol="AAPL",
                        ),
                    ],
                    {
                        "results": [
                            {
                                "symbol": "MSFT",
                                "events": [],
                            },
                        ],
                    },
                    label="Test",
                )
            )

    def test_rejects_wrong_order(
        self,
    ):
        batch = [
            Candidate(
                symbol="AAPL",
                candidate_id="candidate-1",
            ),
            Candidate(
                symbol="MSFT",
                candidate_id="candidate-2",
            ),
        ]

        response = {
            "results": [
                {
                    "symbol": "MSFT",
                    "events": [],
                },
                {
                    "symbol": "AAPL",
                    "events": [],
                },
            ],
        }

        with self.assertRaisesRegex(
            ValueError,
            ("symbol mismatch at index 0: " "expected 'AAPL', received 'MSFT'"),
        ):
            list(
                matched_batch_rows(
                    batch,
                    response,
                    label="Test",
                )
            )

    def test_rejects_non_list_results(
        self,
    ):
        with self.assertRaisesRegex(
            ValueError,
            "results must be a list",
        ):
            list(
                matched_batch_rows(
                    [
                        Candidate(
                            symbol="AAPL",
                        ),
                    ],
                    {
                        "results": {
                            "symbol": "AAPL",
                        },
                    },
                    label="Test",
                )
            )

    def test_rejects_missing_results(
        self,
    ):
        batch = [
            Candidate(
                symbol="AAPL",
                candidate_id="candidate-1",
            ),
            Candidate(
                symbol="MSFT",
                candidate_id="candidate-2",
            ),
        ]

        with self.assertRaisesRegex(
            ValueError,
            ("result count mismatch: " "expected 2, received 0"),
        ):
            list(
                matched_batch_rows(
                    batch,
                    {
                        "results": [],
                    },
                    label="Test",
                )
            )

    def test_rejects_partially_missing_results(
        self,
    ):
        batch = [
            Candidate(
                symbol="AAPL",
                candidate_id="candidate-1",
            ),
            Candidate(
                symbol="MSFT",
                candidate_id="candidate-2",
            ),
        ]

        response = {
            "results": [
                {
                    "symbol": "AAPL",
                    "events": [
                        {
                            "headline": "Evento",
                        },
                    ],
                },
            ],
        }

        with self.assertRaisesRegex(
            ValueError,
            ("result count mismatch: " "expected 2, received 1"),
        ):
            list(
                matched_batch_rows(
                    batch,
                    response,
                    label="Test",
                )
            )

    def test_rejects_extra_results(
        self,
    ):
        batch = [
            Candidate(
                symbol="AAPL",
                candidate_id="candidate-1",
            ),
        ]

        response = {
            "results": [
                {
                    "symbol": "AAPL",
                    "events": [],
                },
                {
                    "symbol": "MSFT",
                    "events": [],
                },
            ],
        }

        with self.assertRaisesRegex(
            ValueError,
            ("result count mismatch: " "expected 1, received 2"),
        ):
            list(
                matched_batch_rows(
                    batch,
                    response,
                    label="Test",
                )
            )

    def test_rejects_non_object_result(
        self,
    ):
        batch = [
            Candidate(
                symbol="AAPL",
                candidate_id="candidate-1",
            ),
        ]

        with self.assertRaisesRegex(
            ValueError,
            ("result at index 0 " "must be an object"),
        ):
            list(
                matched_batch_rows(
                    batch,
                    {
                        "results": [
                            "invalid",
                        ],
                    },
                    label="Test",
                )
            )

    def test_rejects_missing_symbol(
        self,
    ):
        batch = [
            Candidate(
                symbol="AAPL",
                candidate_id="candidate-1",
            ),
        ]

        with self.assertRaisesRegex(
            ValueError,
            ("result at index 0 " "missing symbol"),
        ):
            list(
                matched_batch_rows(
                    batch,
                    {
                        "results": [
                            {
                                "events": [],
                            },
                        ],
                    },
                    label="Test",
                )
            )

    def test_rejects_empty_symbol(
        self,
    ):
        batch = [
            Candidate(
                symbol="AAPL",
                candidate_id="candidate-1",
            ),
        ]

        with self.assertRaisesRegex(
            ValueError,
            ("result at index 0 " "missing symbol"),
        ):
            list(
                matched_batch_rows(
                    batch,
                    {
                        "results": [
                            {
                                "symbol": "",
                                "events": [],
                            },
                        ],
                    },
                    label="Test",
                )
            )


if __name__ == "__main__":
    unittest.main()
