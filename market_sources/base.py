from typing import Protocol

from market_sources.capabilities import SourceProviderCapabilities


class SourceProviderPlugin(Protocol):
    name: str
    capabilities: SourceProviderCapabilities
