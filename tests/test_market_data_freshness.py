import unittest
from datetime import UTC, datetime

from market_data.capabilities import (
    InstrumentType,
    MarketDataCapability,
    ProviderCapabilities,
)
from market_data.config import MarketDataConfig
from market_data.models import InstrumentReference, MarketQuote
from market_data.registry import ProviderRegistry
from market_data.service import MarketDataService


class Fake:
    capabilities = ProviderCapabilities(
        supported=frozenset({MarketDataCapability.LIVE_PRICE})
    )

    def __init__(self, name, q):
        self.name, self.q = name, q

    def get_quote(self, reference):
        return self.q


class TestFreshness(unittest.TestCase):
    def test_stale_falls_back(self):
        now = int(datetime.now(UTC).timestamp())
        cfg = MarketDataConfig(
            {c: ("old", "fresh") for c in MarketDataCapability}, 1, 0, 0, 96
        )
        svc = MarketDataService(
            ProviderRegistry(
                [
                    Fake("old", MarketQuote(now - 864000, 301.1, "EUR", source="old")),
                    Fake("fresh", MarketQuote(now - 60, 286.1, "EUR", source="fresh")),
                ]
            ),
            cfg,
        )
        self.assertEqual(
            svc.get_quote(
                InstrumentReference(
                    symbol="1GOOG.MI", instrument_type=InstrumentType.STOCK
                )
            ).price,
            286.1,
        )
