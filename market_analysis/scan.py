from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from market_analysis.models import ScoreBreakdown, SignalCandidate
from market_analysis.scoring import build_score_breakdown, score_technical_snapshot
from market_analysis.strategies import default_registry
from market_analysis.technical import analyze_price_bars
from market_data.capabilities import InstrumentType
from market_data.models import HistoryRequest, InstrumentReference


class ScanSkipReason(StrEnum):
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    NO_TECHNICAL_SIGNAL = "NO_TECHNICAL_SIGNAL"


@dataclass(frozen=True, slots=True)
class ScanConfig:
    period: str = "1y"
    interval: str = "1d"
    minimum_bars: int = 200
    timeframe: str = "1D"
    enabled_strategies: tuple[str, ...] | None = None

    def __post_init__(self):
        if self.minimum_bars < 2:
            raise ValueError("minimum_bars must be at least 2")


@dataclass(frozen=True, slots=True)
class ScanMember:
    universe_id: str
    instrument_id: str
    entity_id: str
    symbol: str
    exchange: str | None = None
    currency_code: str | None = None
    isin: str | None = None

    def to_reference(self):
        return InstrumentReference(
            symbol=self.symbol,
            isin=self.isin,
            instrument_type=InstrumentType.STOCK,
            currency=self.currency_code,
            exchange_code=self.exchange,
        )


@dataclass(frozen=True, slots=True)
class EvaluatedSignal:
    member: ScanMember
    candidate: SignalCandidate
    scores: ScoreBreakdown


@dataclass(frozen=True, slots=True)
class ScanFailure:
    instrument_id: str
    symbol: str
    reason: str


@dataclass(frozen=True, slots=True)
class ScanSkip:
    instrument_id: str
    symbol: str
    reason: ScanSkipReason
    detail: str
    available_bars: int | None = None
    required_bars: int | None = None

    def to_dict(self):
        return {
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "reason": self.reason.value,
            "detail": self.detail,
            "available_bars": self.available_bars,
            "required_bars": self.required_bars,
        }


@dataclass(frozen=True, slots=True)
class ScanResult:
    processed: int
    candidates: tuple[EvaluatedSignal, ...]
    failures: tuple[ScanFailure, ...]
    skipped: tuple[ScanSkip, ...] = ()

    def to_dict(self):
        return {
            "processed": self.processed,
            "candidates": len(self.candidates),
            "failures": len(self.failures),
            "skipped": len(self.skipped),
            "skipped_by_reason": {
                r.value: sum(1 for x in self.skipped if x.reason == r)
                for r in ScanSkipReason
            },
            "symbols": [x.member.symbol for x in self.candidates],
            "failed_symbols": [x.symbol for x in self.failures],
            "skipped_symbols": [x.symbol for x in self.skipped],
        }


class MarketDataReader(Protocol):
    def get_ohlcv(self, request: HistoryRequest): ...
def _evaluate(member, bars, config):
    snapshot = analyze_price_bars(bars)
    if snapshot is None:
        raise ValueError("technical snapshot unavailable")
    observed = datetime.fromtimestamp(snapshot.observed_timestamp, UTC)
    score = build_score_breakdown(technical=score_technical_snapshot(snapshot))
    return tuple(
        EvaluatedSignal(member, x, score)
        for x in default_registry.evaluate(
            snapshot,
            timeframe=config.timeframe,
            observed_at=observed,
            enabled=config.enabled_strategies,
        )
    )


def evaluate_member(member, *, market_data_service, config, observed_at=None):
    bars = market_data_service.get_ohlcv(
        HistoryRequest(
            instrument=member.to_reference(),
            period=config.period,
            interval=config.interval,
        )
    )
    if len(bars) < config.minimum_bars:
        raise ValueError(
            f"insufficient history: {len(bars)} bars; minimum {config.minimum_bars}"
        )
    values = _evaluate(member, bars, config)
    return values[0] if values else None


def scan_members(members, *, market_data_service, config):
    candidates = []
    failures = []
    skipped = []
    for member in members:
        try:
            bars = market_data_service.get_ohlcv(
                HistoryRequest(
                    instrument=member.to_reference(),
                    period=config.period,
                    interval=config.interval,
                )
            )
            if len(bars) < config.minimum_bars:
                skipped.append(
                    ScanSkip(
                        member.instrument_id,
                        member.symbol,
                        ScanSkipReason.INSUFFICIENT_HISTORY,
                        f"insufficient history: {len(bars)} bars; minimum {config.minimum_bars}",
                        len(bars),
                        config.minimum_bars,
                    )
                )
                continue
            values = _evaluate(member, bars, config)
            if not values:
                skipped.append(
                    ScanSkip(
                        member.instrument_id,
                        member.symbol,
                        ScanSkipReason.NO_TECHNICAL_SIGNAL,
                        "no technical signal generated",
                    )
                )
                continue
            candidates.extend(values)
        except Exception as error:
            failures.append(
                ScanFailure(member.instrument_id, member.symbol, str(error)[:500])
            )
    candidates.sort(
        key=lambda x: (
            -(x.scores.composite or 0),
            -x.candidate.confidence,
            x.member.symbol,
            x.candidate.strategy_code,
        )
    )
    return ScanResult(len(members), tuple(candidates), tuple(failures), tuple(skipped))
