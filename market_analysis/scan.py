from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol
from zoneinfo import ZoneInfo

from market_analysis.models import ScoreBreakdown, SignalCandidate
from market_analysis.scoring import build_score_breakdown, score_technical_snapshot
from market_analysis.strategies import default_registry
from market_analysis.strategies.registry import create_registry
from market_analysis.technical import TechnicalConfig, analyze_price_bars
from market_data.capabilities import InstrumentType
from market_data.models import BatchResult, HistoryRequest, InstrumentReference


class ScanSkipReason(StrEnum):
    INSUFFICIENT_HISTORY = "INSUFFICIENT_HISTORY"
    NO_TECHNICAL_SIGNAL = "NO_TECHNICAL_SIGNAL"


@dataclass(frozen=True, slots=True)
class ScanConfig:
    period: str = "1y"
    interval: str = "1d"
    minimum_bars: int = 200
    instrument_batch_size: int = 20
    timeframe: str = "1D"
    enabled_strategies: tuple[str, ...] | None = None
    technical_config: TechnicalConfig = field(default_factory=TechnicalConfig)
    smc_threshold: float = 0.0005
    minimum_drop_pct: float = 0.0

    def __post_init__(self):
        if self.minimum_bars < 2:
            raise ValueError("minimum_bars must be at least 2")
        if self.instrument_batch_size < 1:
            raise ValueError("instrument_batch_size must be at least 1")

    @classmethod
    def from_settings(cls, settings):
        strategies = str(settings.get("MARKET_ANALYSIS_STRATEGIES", "") or "")
        return cls(
            period=str(settings.get("MARKET_ANALYSIS_PERIOD", "1y")),
            interval=str(settings.get("MARKET_ANALYSIS_INTERVAL", "1d")),
            minimum_bars=int(settings.get("MARKET_ANALYSIS_MINIMUM_BARS", 200)),
            instrument_batch_size=int(
                settings.get("MARKET_ANALYSIS_INSTRUMENT_BATCH_SIZE", 20)
            ),
            timeframe=str(settings.get("MARKET_ANALYSIS_TIMEFRAME", "1D")),
            enabled_strategies=tuple(
                value.strip().upper()
                for value in strategies.split(",")
                if value.strip()
            )
            or None,
            technical_config=TechnicalConfig(
                supertrend_period=int(settings.get("SUPERTREND_ATR_LENGTH", 10)),
                supertrend_factor=float(settings.get("SUPERTREND_FACTOR", 3)),
                supertrend_use_rma=str(
                    settings.get("SUPERTREND_USE_RMA", "true")
                ).lower()
                in {"true", "1", "yes"},
                swing_length=int(settings.get("SMC_SWING_LENGTH", 50)),
                order_block_lookback=int(settings.get("SMC_OB_LOOKBACK", 10)),
                fvg_threshold=float(settings.get("SMC_FVG_THRESHOLD", 0.0001)),
                high_lookback=int(settings.get("MARKET_ANALYSIS_HIGH_LOOKBACK", 126)),
            ),
            smc_threshold=float(settings.get("SMC_SIGNAL_THRESHOLD", 0.0005)),
            minimum_drop_pct=float(
                settings.get("MARKET_ANALYSIS_OVERSOLD_MIN_DROP", 0)
            ),
        )


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
class ScanEvaluation:
    instrument_id: str
    observed_at: datetime
    strategy_codes: tuple[str, ...]
    timeframe: str
    snapshot: dict = field(default_factory=dict)
    input_fingerprint: str | None = None
    bar_count: int = 0


@dataclass(frozen=True, slots=True)
class ScanResult:
    processed: int
    candidates: tuple[EvaluatedSignal, ...]
    failures: tuple[ScanFailure, ...]
    skipped: tuple[ScanSkip, ...] = ()
    evaluations: tuple[ScanEvaluation, ...] = ()

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
            "evaluated_instrument_ids": sorted(
                {str(evaluation.instrument_id) for evaluation in self.evaluations}
            ),
        }


class MarketDataReader(Protocol):
    def get_ohlcv(self, request: HistoryRequest): ...
def _evaluate(member, bars, config, snapshot=None):
    if snapshot is None:
        snapshot = analyze_price_bars(bars, config=config.technical_config)
    if snapshot is None:
        raise ValueError("technical snapshot unavailable")
    observed = datetime.fromtimestamp(snapshot.observed_timestamp, UTC)
    score = build_score_breakdown(technical=score_technical_snapshot(snapshot))
    return tuple(
        EvaluatedSignal(member, x, score)
        for x in create_registry(
            smc_threshold=config.smc_threshold, minimum_drop_pct=config.minimum_drop_pct
        ).evaluate(
            snapshot,
            timeframe=config.timeframe,
            observed_at=observed,
            enabled=config.enabled_strategies,
        )
    )


def _as_utc(value):
    if value is None:
        return datetime.now(UTC)
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _completed_bars(bars, config, observed_at, timezone_name=None):
    if config.interval.lower() not in {"d", "1d", "1day"}:
        return bars
    timezone = ZoneInfo(timezone_name) if timezone_name else UTC
    today = observed_at.astimezone(timezone).date()
    return [
        bar
        for bar in bars
        if datetime.fromtimestamp(bar.timestamp, UTC).astimezone(timezone).date()
        < today
    ]


def evaluate_member(
    member, *, market_data_service, config, observed_at=None, exchange_timezone=None
):
    observed_at = _as_utc(observed_at)
    bars = market_data_service.get_ohlcv(
        HistoryRequest(
            instrument=member.to_reference(),
            period=config.period,
            interval=config.interval,
        )
    )
    bars = _completed_bars(bars, config, observed_at, exchange_timezone)
    if len(bars) < config.minimum_bars:
        raise ValueError(
            f"insufficient history: {len(bars)} bars; minimum {config.minimum_bars}"
        )
    values = _evaluate(member, bars, config)
    return values[0] if values else None


def scan_members(
    members,
    *,
    market_data_service,
    config,
    observed_at=None,
    exchange_timezones=None,
):
    observed_at = _as_utc(observed_at)
    candidates = []
    failures = []
    skipped = []
    evaluations = []
    get_ohlcv_batch = getattr(market_data_service, "get_ohlcv_batch", None)
    for batch_start in range(0, len(members), config.instrument_batch_size):
        batch_members = members[
            batch_start : batch_start + config.instrument_batch_size
        ]
        requests = [
            HistoryRequest(
                instrument=member.to_reference(),
                period=config.period,
                interval=config.interval,
            )
            for member in batch_members
        ]
        if get_ohlcv_batch is None:
            fetch_results = []
            for request in requests:
                try:
                    fetch_results.append(
                        BatchResult(value=market_data_service.get_ohlcv(request))
                    )
                except Exception as error:
                    fetch_results.append(BatchResult(error=error))
        else:
            try:
                fetch_results = get_ohlcv_batch(requests)
                if len(fetch_results) != len(batch_members):
                    raise ValueError(
                        "OHLCV batch result count does not match request count"
                    )
            except Exception as error:
                fetch_results = [
                    BatchResult(error=error) for _ in batch_members
                ]

        for member, fetch_result in zip(batch_members, fetch_results, strict=True):
            try:
                if fetch_result.error is not None:
                    raise fetch_result.error
                bars = fetch_result.value
                if bars is None:
                    raise ValueError("OHLCV batch result has no value")
                timezone_name = (exchange_timezones or {}).get(member.exchange)
                bars = _completed_bars(bars, config, observed_at, timezone_name)
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
                bars = sorted(bars, key=lambda bar: bar.timestamp)
                snapshot = analyze_price_bars(bars, config=config.technical_config)
                if snapshot is None:
                    raise ValueError("technical snapshot unavailable")
                values = _evaluate(member, bars, config, snapshot)
                evaluations.append(
                    ScanEvaluation(
                        member.instrument_id,
                        datetime.fromtimestamp(snapshot.observed_timestamp, UTC),
                        config.enabled_strategies or default_registry.codes,
                        config.timeframe,
                        asdict(snapshot),
                        hashlib.sha256(
                            json.dumps(
                                [asdict(bar) for bar in bars],
                                sort_keys=True,
                                allow_nan=False,
                            ).encode("utf-8")
                        ).hexdigest(),
                        len(bars),
                    )
                )
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
    return ScanResult(
        len(members),
        tuple(candidates),
        tuple(failures),
        tuple(skipped),
        tuple(evaluations),
    )
