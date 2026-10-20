from typing import Protocol

from market_data.capabilities import ProviderCapabilities


class ProviderPlugin(Protocol):
    name: str
    capabilities: ProviderCapabilities
