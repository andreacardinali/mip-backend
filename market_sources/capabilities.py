from dataclasses import dataclass
from enum import StrEnum


class MarketSourceCapability(StrEnum):
    UNIVERSE_MEMBERS = "universe_members"
    DISCOVERY = "discovery"


@dataclass(frozen=True, slots=True)
class SourceProviderCapabilities:
    supported: frozenset[MarketSourceCapability]

    def supports(self, capability: MarketSourceCapability) -> bool:
        return capability in self.supported

    def to_dict(self) -> dict:
        return {item.value: {} for item in sorted(self.supported, key=str)}
