from typing import Protocol

from market_sources.capabilities import SourceProviderCapabilities
from market_sources.models import UniverseMember, UniverseSnapshot


class UniverseProvider(Protocol):
    name: str
    capabilities: SourceProviderCapabilities

    def get_universe_members(self, universe_code: str) -> list[UniverseMember]: ...
    def get_universe_snapshot(self, universe_code: str) -> UniverseSnapshot: ...
