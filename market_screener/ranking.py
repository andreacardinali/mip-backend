from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from market_screener.config import ScreenerRankingConfig
from market_screener.consensus import build_strategy_consensus
from market_screener.models import RankedOpportunity


def _score(value: object) -> float | None:
    if value is None:
        return None
    return max(0.0, min(100.0, float(value)))


def _weighted_score(
    row: Mapping[str, Any],
    config: ScreenerRankingConfig,
) -> float:
    components = (
        (_score(row.get("attention_score")), config.attention_weight),
        (_score(row.get("technical_score")), config.technical_weight),
        (_score(row.get("quant_score")), config.quant_weight),
        (_score(row.get("llm_score")), config.llm_weight),
    )
    available = [
        (value, weight)
        for value, weight in components
        if value is not None and weight > 0
    ]
    if not available:
        return 0.0

    weight_total = sum(weight for _, weight in available)
    base_score = sum(value * weight for value, weight in available) / weight_total

    # Consensus is meaningful only when at least two strategies independently
    # evaluated the same instrument and timeframe. A single signal preserves
    # the legacy weighted score and receives neither bonus nor penalty.
    strategy_count = int(row.get("strategy_count") or 1)
    if strategy_count < 2:
        return round(base_score, 4)

    consensus_bonus = config.consensus_bonus * (
        float(row.get("consensus_score") or 0.0) / 100.0
    )
    conflict_penalty = config.conflict_penalty * (
        float(row.get("conflict_score") or 0.0) / 100.0
    )
    return round(
        max(0.0, min(100.0, base_score + consensus_bonus - conflict_penalty)),
        4,
    )


def rank_opportunities(
    rows: Iterable[Mapping[str, Any]],
    *,
    config: ScreenerRankingConfig,
) -> tuple[RankedOpportunity, ...]:
    prepared: list[tuple[Mapping[str, Any], float]] = []
    for row in build_strategy_consensus(rows):
        score = _weighted_score(row, config)
        if score >= config.minimum_score:
            prepared.append((row, score))

    prepared.sort(key=lambda item: (-item[1], str(item[0].get("symbol") or "")))
    results: list[RankedOpportunity] = []
    for position, (row, score) in enumerate(prepared[: config.limit], start=1):
        results.append(
            RankedOpportunity(
                signal_id=str(row["signal_id"]),
                instrument_id=str(row["instrument_id"]),
                symbol=str(row["symbol"]),
                direction=str(row["direction"]),
                origin=(str(row["origin"]) if row.get("origin") else None),
                attention_score=_score(row.get("attention_score")),
                technical_score=_score(row.get("technical_score")),
                quant_score=_score(row.get("quant_score")),
                llm_score=_score(row.get("llm_score")),
                composite_score=score,
                ranking_position=position,
                verdict=(str(row["verdict"]) if row.get("verdict") else None),
                summary=(str(row["summary"]) if row.get("summary") else None),
                evidence=dict(row.get("evidence") or {}),
                strategy_count=int(row["strategy_count"]),
                bullish_strategy_count=int(row["bullish_strategy_count"]),
                bearish_strategy_count=int(row["bearish_strategy_count"]),
                consensus_direction=str(row["consensus_direction"]),
                consensus_score=float(row["consensus_score"]),
                conflict_score=float(row["conflict_score"]),
                supporting_strategies=tuple(row["supporting_strategies"]),
                opposing_strategies=tuple(row["opposing_strategies"]),
                supporting_signal_ids=tuple(row["supporting_signal_ids"]),
            )
        )
    return tuple(results)
