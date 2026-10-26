from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from market_analysis.models import ScoreBreakdown, SignalCandidate
from market_analysis.scoring import build_score_breakdown, score_technical_snapshot
from market_analysis.signals import generate_trend_pullback_candidate
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
    algorithm_version: str = "market-analysis-v1"

    def __post_init__(self) -> None:
        if self.minimum_bars < 2:
            raise ValueError("minimum_bars must be at least 2")
        if not self.period.strip() or not self.interval.strip():
            raise ValueError("period and interval cannot be empty")


@dataclass(frozen=True, slots=True)
class ScanMember:
    universe_id: str
    instrument_id: str
    entity_id: str
    symbol: str
    exchange: str | None = None
    currency_code: str | None = None
    isin: str | None = None

    def to_reference(self) -> InstrumentReference:
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

    def to_dict(self) -> dict[str, Any]:
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

    def to_dict(self) -> dict[str, Any]:
        skipped_by_reason = {
            reason.value: sum(1 for item in self.skipped if item.reason == reason)
            for reason in ScanSkipReason
        }
        return {
            "processed": self.processed,
            "candidates": len(self.candidates),
            "failures": len(self.failures),
            "skipped": len(self.skipped),
            "skipped_by_reason": skipped_by_reason,
            "symbols": [item.member.symbol for item in self.candidates],
            "failed_symbols": [item.symbol for item in self.failures],
            "skipped_symbols": [item.symbol for item in self.skipped],
        }


class MarketDataReader(Protocol):
    def get_ohlcv(self, request: HistoryRequest): ...


def evaluate_member(
    member: ScanMember,
    *,
    market_data_service: MarketDataReader,
    config: ScanConfig,
    observed_at: datetime | None = None,
) -> EvaluatedSignal | None:
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

    snapshot = analyze_price_bars(bars)
    if snapshot is None:
        raise ValueError("technical snapshot unavailable")

    candidate = generate_trend_pullback_candidate(
        snapshot,
        timeframe=config.timeframe,
        observed_at=observed_at
        or datetime.fromtimestamp(snapshot.observed_timestamp, UTC),
    )
    if candidate is None:
        return None

    scores = build_score_breakdown(
        technical=score_technical_snapshot(snapshot),
    )
    return EvaluatedSignal(member=member, candidate=candidate, scores=scores)


def scan_members(
    members: list[ScanMember],
    *,
    market_data_service: MarketDataReader,
    config: ScanConfig,
) -> ScanResult:
    candidates: list[EvaluatedSignal] = []
    failures: list[ScanFailure] = []
    skipped: list[ScanSkip] = []

    for member in members:
        try:
            bars = market_data_service.get_ohlcv(
                HistoryRequest(
                    instrument=member.to_reference(),
                    period=config.period,
                    interval=config.interval,
                )
            )
            available_bars = len(bars)
            if available_bars < config.minimum_bars:
                skipped.append(
                    ScanSkip(
                        instrument_id=member.instrument_id,
                        symbol=member.symbol,
                        reason=ScanSkipReason.INSUFFICIENT_HISTORY,
                        detail=(
                            f"insufficient history: {available_bars} bars; "
                            f"minimum {config.minimum_bars}"
                        ),
                        available_bars=available_bars,
                        required_bars=config.minimum_bars,
                    )
                )
                continue

            snapshot = analyze_price_bars(bars)
            if snapshot is None:
                raise ValueError("technical snapshot unavailable")

            candidate = generate_trend_pullback_candidate(
                snapshot,
                timeframe=config.timeframe,
                observed_at=datetime.fromtimestamp(
                    snapshot.observed_timestamp,
                    UTC,
                ),
            )
            if candidate is None:
                skipped.append(
                    ScanSkip(
                        instrument_id=member.instrument_id,
                        symbol=member.symbol,
                        reason=ScanSkipReason.NO_TECHNICAL_SIGNAL,
                        detail="no technical signal generated",
                    )
                )
                continue

            candidates.append(
                EvaluatedSignal(
                    member=member,
                    candidate=candidate,
                    scores=build_score_breakdown(
                        technical=score_technical_snapshot(snapshot),
                    ),
                )
            )
        except Exception as error:
            failures.append(
                ScanFailure(
                    instrument_id=member.instrument_id,
                    symbol=member.symbol,
                    reason=str(error)[:500],
                )
            )

    candidates.sort(
        key=lambda item: (
            -(item.scores.composite or 0.0),
            -item.candidate.confidence,
            item.member.symbol,
        )
    )
    return ScanResult(
        processed=len(members),
        candidates=tuple(candidates),
        failures=tuple(failures),
        skipped=tuple(skipped),
    )
