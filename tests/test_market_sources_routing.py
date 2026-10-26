from market_sources.capabilities import (
    MarketSourceCapability,
    SourceProviderCapabilities,
)
from market_sources.config import MarketSourceConfig
from market_sources.models import UniverseMember
from market_sources.registry import SourceProviderRegistry
from market_sources.service import MarketSourceService


class Fake:
    name = "fake"
    capabilities = SourceProviderCapabilities(
        frozenset({MarketSourceCapability.UNIVERSE_MEMBERS})
    )

    def get_universe_members(self, code):
        return [UniverseMember(code, "AAPL", source="FAKE")]


def test_routes_universe_members():
    config = MarketSourceConfig(
        {
            MarketSourceCapability.UNIVERSE_MEMBERS: ("fake",),
            MarketSourceCapability.DISCOVERY: (),
        },
        1,
        0,
        0,
    )
    assert (
        MarketSourceService(SourceProviderRegistry([Fake()]), config)
        .get_universe_members("TEST")[0]
        .symbol
        == "AAPL"
    )
