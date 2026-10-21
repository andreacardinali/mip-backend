import unittest
from dataclasses import dataclass
from datetime import date, datetime
from types import SimpleNamespace

from jobs.portfolio_intelligence.evidence import (
    MarketEvidenceCollector,
    instrument_reference,
)
from jobs.portfolio_intelligence.models import EntityCandidate
from market_data.capabilities import InstrumentType


@dataclass(frozen=True)
class Item:
    name: str
    source: str | None = None
    published_at: datetime | None = None


class FakeMarketData:
    def __init__(self, fail_news=False):
        self.fail_news = fail_news
        self.earnings_dates = []

    def get_news(self, reference, limit=20):
        if self.fail_news:
            raise RuntimeError("unavailable")
        return [Item("headline", "provider", datetime(2026, 10, 21, 12, 0))]

    def get_economic_calendar(self, limit=None):
        return [Item("CPI")]

    def get_earnings_calendar(self, event_date, symbol=None):
        self.earnings_dates.append(event_date)
        return [Item(f"earnings-{event_date}")]

    def get_etf_holdings(self, reference, limit=None):
        return [Item("Holding")]

    def get_etf_countries(self, reference):
        return [Item("Switzerland")]

    def get_etf_sectors(self, reference):
        return [Item("Technology")]


class Phase2EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.config = SimpleNamespace(
            market_evidence_enabled=True,
            news_limit=10,
            economic_event_limit=20,
            etf_holding_limit=15,
        )

    def candidate(self, instrument_type="STOCK"):
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

    def test_reference_preserves_listing(self):
        ref = instrument_reference(self.candidate())
        self.assertEqual(ref.exchange_code, "XSWX")
        self.assertEqual(ref.currency, "CHF")
        self.assertEqual(ref.instrument_type, InstrumentType.STOCK)

    def test_news_failure_is_best_effort(self):
        collector = MarketEvidenceCollector(FakeMarketData(fail_news=True), self.config)
        evidence = collector.for_breaking(self.candidate())
        self.assertEqual(evidence.news, ())
        self.assertEqual(collector.diagnostics.failures["news"], 1)

    def test_upcoming_stock_scans_each_day(self):
        market_data = FakeMarketData()
        collector = MarketEvidenceCollector(market_data, self.config)
        evidence = collector.for_upcoming(
            self.candidate(), date(2026, 10, 21), date(2026, 10, 23)
        )
        self.assertEqual(len(evidence.earnings), 3)
        self.assertEqual(len(market_data.earnings_dates), 3)

    def test_etf_collects_holdings_and_exposures(self):
        collector = MarketEvidenceCollector(FakeMarketData(), self.config)
        evidence = collector.for_upcoming(
            self.candidate("ETF"), date(2026, 10, 21), date(2026, 10, 21)
        )
        self.assertEqual(len(evidence.etf_holdings), 1)
        self.assertEqual(len(evidence.etf_countries), 1)
        self.assertEqual(len(evidence.etf_sectors), 1)
        self.assertEqual(evidence.earnings, ())


if __name__ == "__main__":
    unittest.main()
