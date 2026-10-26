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
    supertrend_alignment_bonus: float = 4.0
    smc_choch_bonus: float = 5.0
    smc_bos_bonus: float = 7.0
    mean_reversion_bonus: float = 3.0
    score_floor: float = 0.0
    score_ceiling: float = 100.0

    def __post_init__(self):
        if self.limit < 1:
            raise ValueError("limit must be positive")
        if self.score_ceiling <= self.score_floor:
            raise ValueError("score_ceiling must exceed score_floor")


@dataclass(frozen=True, slots=True)
class QuantPrefilterResult:
    evaluated: EvaluatedSignal
    quant_score: float
    breakdown: dict[str, float]


def _finite(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _direction(evaluated):
    return 1 if evaluated.candidate.direction.value == "LONG" else -1


def _directional_marker(value, direction):
    marker = _finite(value)
    if marker not in (-1.0, 1.0):
        return 0.0
    return 1.0 if marker == direction else -1.0


def score_evaluated_signal(evaluated: EvaluatedSignal, *, config: QuantPrefilterConfig):
    evidence = evaluated.candidate.evidence or {}
    technical = evidence.get("technical") or evidence
    direction = _direction(evaluated)
    signal_type = evaluated.candidate.signal_type.value
    strategy = evaluated.candidate.strategy_code.upper()
    components = {}
    components["signal"] = float(config.signal_scores.get(signal_type, 0.0))
    rsi = _finite(technical.get("rsi"))
    rsi_component = 0.0
    if rsi is not None:
        directional = ((rsi - 50.0) / 20.0) * direction
        if strategy in {"PULLBACK_DEEP_OVERSOLD", "CALL_ENTRY_SETUP"}:
            directional *= -1
        rsi_component = max(-2.0, min(directional, 2.0)) * config.rsi_weight
    components["rsi"] = rsi_component
    daily = _finite(technical.get("dailyChangePct", technical.get("daily_change_pct")))
    day = 0.0
    if daily is not None:
        day = max(-2.0, min((daily / 3.0) * direction, 2.0)) * config.day_move_weight
    components["day_move"] = day
    close = _finite(technical.get("marketPrice", technical.get("market_price")))
    s50 = _finite(technical.get("sma50", technical.get("sma_50")))
    s200 = _finite(technical.get("sma200", technical.get("sma_200")))
    trend = 0.0
    if close and s50:
        trend += max(-2.0, min(((close / s50) - 1.0) * 20.0 * direction, 2.0))
    if s50 and s200:
        trend += max(-2.0, min(((s50 / s200) - 1.0) * 20.0 * direction, 2.0))
    components["trend"] = trend * config.trend_weight
    width = _finite(technical.get("bbWidth", technical.get("bollinger_band_width_pct")))
    components["bb_squeeze"] = (
        config.bb_squeeze_bonus
        if width is not None and width <= config.bb_width_max
        else 0.0
    )
    components["confidence"] = evaluated.candidate.confidence * config.confidence_weight
    technical_score = _finite(evaluated.scores.technical) or 0.0
    components["technical_score"] = (
        technical_score / 100.0
    ) * config.technical_score_weight
    st = technical.get("supertrendDirection", technical.get("supertrend_direction"))
    components["supertrend_alignment"] = (
        _directional_marker(st, direction) * config.supertrend_alignment_bonus
    )
    choch = technical.get("changeOfCharacter", technical.get("change_of_character"))
    components["smc_choch"] = (
        _directional_marker(choch, direction) * config.smc_choch_bonus
    )
    bos = technical.get("breakOfStructure", technical.get("break_of_structure"))
    components["smc_bos"] = _directional_marker(bos, direction) * config.smc_bos_bonus
    components["mean_reversion"] = (
        config.mean_reversion_bonus
        if strategy in {"PULLBACK_DEEP_OVERSOLD", "CALL_ENTRY_SETUP"}
        else 0.0
    )
    raw = sum(components.values())
    score = max(config.score_floor, min(config.score_ceiling, raw))
    components["raw_total"] = raw
    return round(score, 4), {k: round(v, 4) for k, v in components.items()}


def prefilter_evaluated_signals(
    evaluated_signals: Iterable[EvaluatedSignal], *, config: QuantPrefilterConfig
):
    results = []
    for evaluated in evaluated_signals:
        quant_score, breakdown = score_evaluated_signal(evaluated, config=config)
        if quant_score >= config.minimum_score:
            results.append(QuantPrefilterResult(evaluated, quant_score, breakdown))
    results.sort(
        key=lambda x: (
            -x.quant_score,
            x.evaluated.member.symbol,
            x.evaluated.candidate.strategy_code,
        )
    )
    return tuple(results[: config.limit])
