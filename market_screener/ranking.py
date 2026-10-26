from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from market_screener.config import ScreenerRankingConfig
from market_screener.models import RankedOpportunity


def _score(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)
    return max(0.0, min(100.0, number))


def _weighted_score(row: Mapping[str, Any], config: ScreenerRankingConfig) -> float:
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
    return round(sum(value * weight for value, weight in available) / weight_total, 4)


def rank_opportunities(
    rows: Iterable[Mapping[str, Any]],
    *,
    config: ScreenerRankingConfig,
) -> tuple[RankedOpportunity, ...]:
    prepared: list[tuple[Mapping[str, Any], float]] = []
    for row in rows:
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
            )
        )
    return tuple(results)
