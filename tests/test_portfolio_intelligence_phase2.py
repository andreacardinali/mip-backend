import unittest
from dataclasses import dataclass
from datetime import date, datetime
from types import SimpleNamespace

from jobs.portfolio_intelligence.evidence import (
    MarketEvidenceCollector,
    instrument_reference,
)
from jobs.portfolio_intelligence.models import (
    EntityCandidate,
)
from market_data.capabilities import InstrumentType


@dataclass(frozen=True)
class Item:
    name: str
    source: str | None = None
    published_at: datetime | None = None


@dataclass(frozen=True)
class EarningsItem:
    symbol: str
    event_date: date
    fiscal_year: int | None = None
    fiscal_quarter: int | None = None
    timing: str | None = None
    eps_actual: float | None = None
    eps_estimate: float | None = None
    revenue_actual: float | None = None
    revenue_estimate: float | None = None
    isin: str | None = None
    company_name: str | None = None
    source: str = "test"


class FakeMarketData:
    def __init__(
        self,
        fail_news: bool = False,
        *,
        earnings_factory=None,
    ):
        self.fail_news = fail_news
        self.earnings_factory = earnings_factory
        self.earnings_dates: list[date] = []

    def get_news(
        self,
        reference,
        limit=20,
    ):
        if self.fail_news:
            raise RuntimeError("unavailable")

        return [
            Item(
                "headline",
                "provider",
                datetime(
                    2026,
                    10,
                    21,
                    12,
                    0,
                ),
            )
        ]

    def get_economic_calendar(
        self,
        limit=None,
    ):
        return [Item("CPI")]

    def get_earnings_calendar(
        self,
        event_date,
        symbol=None,
    ):
        self.earnings_dates.append(event_date)

        if self.earnings_factory is not None:
            return self.earnings_factory(
                event_date,
                symbol,
            )

        return [
            EarningsItem(
                symbol=symbol or "TEST",
                event_date=event_date,
                fiscal_year=event_date.year,
                fiscal_quarter=4,
            )
        ]

    def get_etf_holdings(
        self,
        reference,
        limit=None,
    ):
        return [Item("Holding")]

    def get_etf_countries(
        self,
        reference,
    ):
        return [Item("Switzerland")]

    def get_etf_sectors(
        self,
        reference,
    ):
        return [Item("Technology")]


class Phase2EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            market_evidence_enabled=True,
            news_limit=10,
            economic_event_limit=20,
            etf_holding_limit=15,
        )

    def candidate(
        self,
        instrument_type="STOCK",
    ):
        return EntityCandidate(
            user_id="u",
            entity_id="e",
            instrument_id="i",
            symbol="TEST",
            name="Test",
            exchange="XSWX",
            currency="CHF",
            instrument_type=instrument_type,
        )

    def test_reference_preserves_listing(
        self,
    ):
        ref = instrument_reference(self.candidate())

        self.assertEqual(
            ref.exchange_code,
            "XSWX",
        )
        self.assertEqual(
            ref.currency,
            "CHF",
        )
        self.assertEqual(
            ref.instrument_type,
            InstrumentType.STOCK,
        )

    def test_news_failure_is_best_effort(
        self,
    ):
        collector = MarketEvidenceCollector(
            FakeMarketData(fail_news=True),
            self.config,
        )

        evidence = collector.for_breaking(self.candidate())

        self.assertEqual(
            evidence.news,
            (),
        )
        self.assertEqual(
            collector.diagnostics.failures["news"],
            1,
        )

    def test_upcoming_stock_scans_each_day(
        self,
    ):
        market_data = FakeMarketData()

        collector = MarketEvidenceCollector(
            market_data,
            self.config,
        )

        evidence = collector.for_upcoming(
            self.candidate(),
            date(2026, 10, 21),
            date(2026, 10, 23),
        )

        self.assertEqual(
            market_data.earnings_dates,
            [
                date(2026, 10, 21),
                date(2026, 10, 22),
                date(2026, 10, 23),
            ],
        )

        self.assertEqual(
            len(evidence.earnings),
            3,
        )

        self.assertEqual(
            [item["event_date"] for item in evidence.earnings],
            [
                "2026-10-21",
                "2026-10-22",
                "2026-10-23",
            ],
        )

    def test_upcoming_deduplicates_earnings(
        self,
    ):
        event = EarningsItem(
            symbol="TEST",
            event_date=date(
                2026,
                10,
                22,
            ),
            fiscal_year=2026,
            fiscal_quarter=3,
        )

        market_data = FakeMarketData(
            earnings_factory=(
                lambda event_date, symbol: [
                    event,
                    event,
                ]
            )
        )

        collector = MarketEvidenceCollector(
            market_data,
            self.config,
        )

        evidence = collector.for_upcoming(
            self.candidate(),
            date(2026, 10, 21),
            date(2026, 10, 23),
        )

        self.assertEqual(
            len(market_data.earnings_dates),
            3,
        )
        self.assertEqual(
            len(evidence.earnings),
            1,
        )
        self.assertEqual(
            evidence.earnings[0]["symbol"],
            "TEST",
        )
        self.assertEqual(
            evidence.earnings[0]["event_date"],
            "2026-10-22",
        )

    def test_upcoming_removes_earnings_outside_window(
        self,
    ):
        market_data = FakeMarketData(
            earnings_factory=(
                lambda event_date, symbol: [
                    EarningsItem(
                        symbol="TEST",
                        event_date=date(
                            2026,
                            10,
                            20,
                        ),
                        fiscal_year=2026,
                        fiscal_quarter=3,
                    ),
                    EarningsItem(
                        symbol="TEST",
                        event_date=date(
                            2026,
                            10,
                            22,
                        ),
                        fiscal_year=2026,
                        fiscal_quarter=3,
                    ),
                    EarningsItem(
                        symbol="TEST",
                        event_date=date(
                            2026,
                            11,
                            5,
                        ),
                        fiscal_year=2026,
                        fiscal_quarter=3,
                    ),
                ]
            )
        )

        collector = MarketEvidenceCollector(
            market_data,
            self.config,
        )

        evidence = collector.for_upcoming(
            self.candidate(),
            date(2026, 10, 21),
            date(2026, 10, 23),
        )

        self.assertEqual(
            len(evidence.earnings),
            1,
        )
        self.assertEqual(
            evidence.earnings[0]["event_date"],
            "2026-10-22",
        )

    def test_upcoming_removes_other_symbols(
        self,
    ):
        market_data = FakeMarketData(
            earnings_factory=(
                lambda event_date, symbol: [
                    EarningsItem(
                        symbol="OTHER",
                        event_date=date(
                            2026,
                            10,
                            22,
                        ),
                        fiscal_year=2026,
                        fiscal_quarter=3,
                    ),
                    EarningsItem(
                        symbol="TEST",
                        event_date=date(
                            2026,
                            10,
                            22,
                        ),
                        fiscal_year=2026,
                        fiscal_quarter=3,
                    ),
                ]
            )
        )

        collector = MarketEvidenceCollector(
            market_data,
            self.config,
        )

        evidence = collector.for_upcoming(
            self.candidate(),
            date(2026, 10, 21),
            date(2026, 10, 23),
        )

        self.assertEqual(
            len(evidence.earnings),
            1,
        )
        self.assertEqual(
            evidence.earnings[0]["symbol"],
            "TEST",
        )

    def test_upcoming_skips_invalid_event_date(
        self,
    ):
        invalid = {
            "symbol": "TEST",
            "event_date": "invalid-date",
            "fiscal_year": 2026,
            "fiscal_quarter": 3,
        }

        market_data = FakeMarketData(
            earnings_factory=(
                lambda event_date, symbol: [
                    invalid,
                ]
            )
        )

        collector = MarketEvidenceCollector(
            market_data,
            self.config,
        )

        evidence = collector.for_upcoming(
            self.candidate(),
            date(2026, 10, 21),
            date(2026, 10, 23),
        )

        self.assertEqual(
            evidence.earnings,
            (),
        )

    def test_etf_collects_holdings_and_exposures(
        self,
    ):
        collector = MarketEvidenceCollector(
            FakeMarketData(),
            self.config,
        )

        evidence = collector.for_upcoming(
            self.candidate("ETF"),
            date(2026, 10, 21),
            date(2026, 10, 21),
        )

        self.assertEqual(
            len(evidence.etf_holdings),
            1,
        )
        self.assertEqual(
            len(evidence.etf_countries),
            1,
        )
        self.assertEqual(
            len(evidence.etf_sectors),
            1,
        )
        self.assertEqual(
            evidence.earnings,
            (),
        )


if __name__ == "__main__":
    unittest.main()
