from typing import Protocol

from market_sources.capabilities import SourceProviderCapabilities
from market_sources.models import DiscoveryRequest, DiscoverySnapshot


class DiscoveryProvider(Protocol):
    name: str
    capabilities: SourceProviderCapabilities

    def discover(self, request: DiscoveryRequest) -> DiscoverySnapshot: ...
