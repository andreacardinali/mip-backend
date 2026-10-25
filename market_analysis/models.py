from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class SignalDirection(StrEnum):
    LONG = "LONG"
    SHORT = "SHORT"


class SignalStatus(StrEnum):
    CANDIDATE = "CANDIDATE"
    CONFIRMED = "CONFIRMED"
    INVALIDATED = "INVALIDATED"
    EXPIRED = "EXPIRED"
    CLOSED = "CLOSED"


class SignalType(StrEnum):
    STRONG_BUY = "STRONG_BUY"
    BUY = "BUY"
    NEUTRAL = "NEUTRAL"
    SELL = "SELL"
    STRONG_SELL = "STRONG_SELL"


class TrendType(StrEnum):
    STRONG_BULLISH = "STRONG_BULLISH"
    BULLISH = "BULLISH"
    SIDEWAYS = "SIDEWAYS"
    BEARISH = "BEARISH"
    STRONG_BEARISH = "STRONG_BEARISH"


@dataclass(frozen=True, slots=True)
class TechnicalSnapshot:
    market_price: float
    previous_close: float
    daily_change_pct: float
    observed_timestamp: int
    rsi: float | None
    sma_50: float | None
    sma_200: float | None
    bollinger_band_width_pct: float | None

    def to_dict(self) -> dict[str, Any]:
        """Return the API/prompt-compatible representation used by MIP."""
        return {
            "marketPrice": self.market_price,
            "previousClose": self.previous_close,
            "dailyChangePct": self.daily_change_pct,
            "rsi": self.rsi,
            "sma50": self.sma_50,
            "sma200": self.sma_200,
            "bbWidth": self.bollinger_band_width_pct,
            "observedDate": self.observed_timestamp,
        }


@dataclass(frozen=True, slots=True)
class FinancialSnapshot:
    metrics: dict[str, float] = field(default_factory=dict)
    metric_date: str | None = None

    def get(self, name: str) -> float | None:
        return self.metrics.get(name.upper())

    def to_dict(self) -> dict[str, Any]:
        return {
            "metrics": dict(sorted(self.metrics.items())),
            "metricDate": self.metric_date,
        }


@dataclass(frozen=True, slots=True)
class ScoreBreakdown:
    technical: float | None = None
    financial: float | None = None
    catalyst: float | None = None
    llm: float | None = None
    composite: float | None = None

    def to_dict(self) -> dict[str, float | None]:
        return {
            "technical": self.technical,
            "financial": self.financial,
            "catalyst": self.catalyst,
            "llm": self.llm,
            "composite": self.composite,
        }


@dataclass(frozen=True, slots=True)
class SignalCandidate:
    strategy_code: str
    timeframe: str
    direction: SignalDirection
    signal_type: SignalType
    trend: TrendType
    reference_price: float
    observed_at: datetime
    confidence: float
    reasons: tuple[str, ...]
    evidence: dict[str, Any] = field(default_factory=dict)
    invalidation_price: float | None = None
    target_price: float | None = None

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.reference_price <= 0:
            raise ValueError("reference_price must be greater than zero")
        if not self.strategy_code.strip():
            raise ValueError("strategy_code cannot be empty")
        if not self.timeframe.strip():
            raise ValueError("timeframe cannot be empty")

    def to_payload(self) -> dict[str, Any]:
        return {
            "strategy_code": self.strategy_code,
            "timeframe": self.timeframe,
            "direction": self.direction.value,
            "signal_type": self.signal_type.value,
            "trend": self.trend.value,
            "reference_price": self.reference_price,
            "observed_at": self.observed_at.isoformat(),
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "evidence": self.evidence,
            "invalidation_price": self.invalidation_price,
            "target_price": self.target_price,
        }
