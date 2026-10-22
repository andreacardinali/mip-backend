import unittest
from dataclasses import dataclass
from datetime import date
from types import SimpleNamespace

from jobs.portfolio_intelligence.evidence import (
    MarketEvidenceCollector,
)


@dataclass(frozen=True)
class EarningsEvent:
    symbol: str
    event_date: date
    fiscal_year: int | None
    fiscal_quarter: int | None
    timing: str | None = None
    eps_actual: float | None = None
    eps_estimate: float | None = None
    revenue_actual: float | None = None
    revenue_estimate: float | None = None
    isin: str | None = None
    company_name: str | None = None
    source: str = "test"


class FakeMarketData:
    def __init__(self, earnings):
        self.earnings = earnings
        self.calls = []

    def get_earnings_calendar(
        self,
        event_date,
        *,
        symbol=None,
    ):
        self.calls.append(
            {
                "event_date": event_date,
                "symbol": symbol,
            }
        )

        return list(self.earnings)


class UpcomingEarningsEvidenceTests(unittest.TestCase):
    def test_filters_and_deduplicates_earnings(
        self,
    ):
        in_window = EarningsEvent(
            symbol="SPCX",
            event_date=date(
                2026,
                10,
                25,
            ),
            fiscal_year=2026,
            fiscal_quarter=3,
        )

        outside_window = EarningsEvent(
            symbol="SPCX",
            event_date=date(
                2027,
                2,
                25,
            ),
            fiscal_year=2026,
            fiscal_quarter=4,
        )

        other_symbol = EarningsEvent(
            symbol="MSFT",
            event_date=date(
                2026,
                10,
                25,
            ),
            fiscal_year=2026,
            fiscal_quarter=3,
        )

        market_data = FakeMarketData(
            [
                in_window,
                in_window,
                outside_window,
                other_symbol,
            ]
        )

        config = SimpleNamespace(
            market_evidence_enabled=True,
        )

        collector = MarketEvidenceCollector(
            market_data,
            config,
        )

        candidate = SimpleNamespace(
            symbol="SPCX",
        )

        earnings = collector._upcoming_earnings(
            candidate,
            date(2026, 10, 22),
            date(2026, 10, 29),
        )

        self.assertEqual(
            len(earnings),
            1,
        )

        self.assertEqual(
            earnings[0]["symbol"],
            "SPCX",
        )

        self.assertEqual(
            earnings[0]["event_date"],
            "2026-10-25",
        )
