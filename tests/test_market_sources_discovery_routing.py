from datetime import UTC, datetime

from market_sources.capabilities import (
    MarketSourceCapability,
    SourceProviderCapabilities,
)
from market_sources.config import MarketSourceConfig
from market_sources.models import (
    DiscoveryCandidate,
    DiscoveryRequest,
    DiscoverySnapshot,
    DiscoverySourceType,
)
from market_sources.registry import SourceProviderRegistry
from market_sources.service import MarketSourceService


class FakeDiscoveryProvider:
    name = "fake"
    capabilities = SourceProviderCapabilities(
        frozenset({MarketSourceCapability.DISCOVERY})
    )

    def discover(self, request):
        return DiscoverySnapshot(
            source_type=request.source_type,
            provider_name=self.name,
            observed_at=datetime.now(UTC),
            candidates=(DiscoveryCandidate(symbol="AAPL", rank=1),),
        )


def test_discovery_routes_through_market_source_service() -> None:
    config = MarketSourceConfig(
        provider_orders={
            MarketSourceCapability.UNIVERSE_MEMBERS: ("fake",),
            MarketSourceCapability.DISCOVERY: ("fake",),
        },
        http_timeout=1,
        http_max_retries=0,
        http_retry_base_seconds=0,
    )
    service = MarketSourceService(
        SourceProviderRegistry([FakeDiscoveryProvider()]),
        config,
    )
    snapshot = service.discover(
        DiscoveryRequest(
            source_type=DiscoverySourceType.REDDIT_TRENDING,
            limit=10,
        )
    )
    assert snapshot.provider_name == "fake"
    assert snapshot.candidates[0].symbol == "AAPL"
