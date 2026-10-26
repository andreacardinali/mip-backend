from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any


class DiscoverySourceType(StrEnum):
    REDDIT_TRENDING = "REDDIT_TRENDING"
    GAINERS = "GAINERS"
    LOSERS = "LOSERS"
    MOST_ACTIVE = "MOST_ACTIVE"
    MARKET_MOVERS = "MARKET_MOVERS"
    NEWS_MOMENTUM = "NEWS_MOMENTUM"
    SHORT_INTEREST = "SHORT_INTEREST"
    INSIDER_ACTIVITY = "INSIDER_ACTIVITY"
    UNUSUAL_VOLUME = "UNUSUAL_VOLUME"


@dataclass(frozen=True, slots=True)
class UniverseMember:
    universe_code: str
    symbol: str
    name: str | None = None
    exchange_code: str | None = None
    sector: str | None = None
    industry: str | None = None
    source_reference: str | None = None
    source: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        universe_code = self.universe_code.strip().upper()
        symbol = self.symbol.strip().upper()

        if not universe_code:
            raise ValueError("universe_code cannot be empty")
        if not symbol:
            raise ValueError("symbol cannot be empty")

        object.__setattr__(self, "universe_code", universe_code)
        object.__setattr__(self, "symbol", symbol)


@dataclass(frozen=True, slots=True)
class UniverseSnapshot:
    universe_code: str
    provider_name: str
    observed_at: datetime
    members: tuple[UniverseMember, ...]
    source_metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        code = self.universe_code.strip().upper()
        provider = self.provider_name.strip().lower()

        if not code:
            raise ValueError("universe_code cannot be empty")
        if not provider:
            raise ValueError("provider_name cannot be empty")
        if self.observed_at.tzinfo is None:
            object.__setattr__(
                self,
                "observed_at",
                self.observed_at.replace(tzinfo=UTC),
            )

        object.__setattr__(self, "universe_code", code)
        object.__setattr__(self, "provider_name", provider)

    @property
    def provider(self) -> str:
        return self.provider_name


@dataclass(frozen=True, slots=True)
class DiscoveryRequest:
    source_type: DiscoverySourceType
    limit: int = 100
    filters: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.limit <= 0:
            raise ValueError("limit must be positive")


@dataclass(frozen=True, slots=True)
class DiscoveryCandidate:
    symbol: str
    rank: int | None = None
    score: float | None = None
    previous_rank: int | None = None
    mentions: int | None = None
    source_payload: dict[str, Any] = field(default_factory=dict)
    name: str | None = None
    previous_mentions: int | None = None
    upvotes: int | None = None

    def __post_init__(self) -> None:
        symbol = self.symbol.strip().upper()
        if not symbol:
            raise ValueError("symbol cannot be empty")
        if self.rank is not None and self.rank <= 0:
            raise ValueError("rank must be positive")
        if self.previous_rank is not None and self.previous_rank <= 0:
            raise ValueError("previous_rank must be positive")
        if self.mentions is not None and self.mentions < 0:
            raise ValueError("mentions cannot be negative")
        if self.previous_mentions is not None and self.previous_mentions < 0:
            raise ValueError("previous_mentions cannot be negative")
        if self.upvotes is not None and self.upvotes < 0:
            raise ValueError("upvotes cannot be negative")
        object.__setattr__(self, "symbol", symbol)


@dataclass(frozen=True, slots=True)
class DiscoverySnapshot:
    source_type: DiscoverySourceType
    provider_name: str
    observed_at: datetime
    candidates: tuple[DiscoveryCandidate, ...]
    source_metadata: dict[str, Any] = field(default_factory=dict)
    requested_limit: int | None = None
    source_reference: str | None = None

    def __post_init__(self) -> None:
        provider = self.provider_name.strip().lower()
        if not provider:
            raise ValueError("provider_name cannot be empty")
        if self.observed_at.tzinfo is None:
            object.__setattr__(
                self,
                "observed_at",
                self.observed_at.replace(tzinfo=UTC),
            )
        if self.requested_limit is not None and self.requested_limit <= 0:
            raise ValueError("requested_limit must be positive")
        object.__setattr__(self, "provider_name", provider)

    @property
    def provider(self) -> str:
        return self.provider_name

    @property
    def returned_count(self) -> int:
        return len(self.candidates)

    @property
    def effective_requested_limit(self) -> int:
        return self.requested_limit or len(self.candidates)
