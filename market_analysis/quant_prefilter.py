from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field

from market_analysis.scan import EvaluatedSignal

DEFAULT_SIGNAL_SCORES = {
    "STRONG_BUY": 12.0,
    "BUY": 8.0,
    "NEUTRAL": 0.0,
    "SELL": 8.0,
    "STRONG_SELL": 12.0,
}


@dataclass(frozen=True, slots=True)
class QuantPrefilterConfig:
    minimum_score: float = 0.0
    limit: int = 25
    signal_scores: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_SIGNAL_SCORES)
    )
    rsi_weight: float = 4.0
    day_move_weight: float = 3.0
    trend_weight: float = 4.0
    bb_width_max: float = 5.0
    bb_squeeze_bonus: float = 2.0
    confidence_weight: float = 10.0
    technical_score_weight: float = 10.0

    def __post_init__(self) -> None:
        if self.limit < 1:
            raise ValueError("limit must be positive")


@dataclass(frozen=True, slots=True)
class QuantPrefilterResult:
    evaluated: EvaluatedSignal
    quant_score: float
    breakdown: dict[str, float]


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _direction(evaluated: EvaluatedSignal) -> int:
    return 1 if evaluated.candidate.direction.value == "LONG" else -1


def score_evaluated_signal(
    evaluated: EvaluatedSignal,
    *,
    config: QuantPrefilterConfig,
) -> tuple[float, dict[str, float]]:
    evidence = evaluated.candidate.evidence or {}
    technical = evidence.get("technical") or evidence
    direction = _direction(evaluated)
    signal_type = evaluated.candidate.signal_type.value

    components: dict[str, float] = {}
    components["signal"] = float(config.signal_scores.get(signal_type, 0.0))

    rsi = _finite(technical.get("rsi"))
    rsi_component = 0.0
    if rsi is not None:
        directional_rsi = ((rsi - 50.0) / 20.0) * direction
        rsi_component = max(-2.0, min(directional_rsi, 2.0))
        rsi_component *= config.rsi_weight
    components["rsi"] = rsi_component

    daily_change = _finite(
        technical.get(
            "dailyChangePct",
            technical.get("daily_change_pct"),
        )
    )
    day_component = 0.0
    if daily_change is not None:
        directional_move = (daily_change / 3.0) * direction
        day_component = max(-2.0, min(directional_move, 2.0))
        day_component *= config.day_move_weight
    components["day_move"] = day_component

    close = _finite(technical.get("marketPrice", technical.get("market_price")))
    sma50 = _finite(technical.get("sma50", technical.get("sma_50")))
    sma200 = _finite(technical.get("sma200", technical.get("sma_200")))
    trend_component = 0.0
    if close and sma50:
        trend = ((close / sma50) - 1.0) * 20.0 * direction
        trend_component += max(-2.0, min(trend, 2.0))
    if sma50 and sma200:
        trend = ((sma50 / sma200) - 1.0) * 20.0 * direction
        trend_component += max(-2.0, min(trend, 2.0))
    trend_component *= config.trend_weight
    components["trend"] = trend_component

    bb_width = _finite(
        technical.get(
            "bbWidth",
            technical.get("bollinger_band_width_pct"),
        )
    )
    components["bb_squeeze"] = (
        config.bb_squeeze_bonus
        if bb_width is not None and bb_width <= config.bb_width_max
        else 0.0
    )

    components["confidence"] = evaluated.candidate.confidence * config.confidence_weight
    technical_score = _finite(evaluated.scores.technical) or 0.0
    components["technical_score"] = technical_score * config.technical_score_weight

    score = round(sum(components.values()), 4)
    return score, {key: round(value, 4) for key, value in components.items()}


def prefilter_evaluated_signals(
    evaluated_signals: Iterable[EvaluatedSignal],
    *,
    config: QuantPrefilterConfig,
) -> tuple[QuantPrefilterResult, ...]:
    results: list[QuantPrefilterResult] = []

    for evaluated in evaluated_signals:
        quant_score, breakdown = score_evaluated_signal(
            evaluated,
            config=config,
        )
        if quant_score < config.minimum_score:
            continue
        results.append(
            QuantPrefilterResult(
                evaluated=evaluated,
                quant_score=quant_score,
                breakdown=breakdown,
            )
        )

    results.sort(
        key=lambda item: (
            -item.quant_score,
            item.evaluated.member.symbol,
        )
    )
    return tuple(results[: config.limit])
