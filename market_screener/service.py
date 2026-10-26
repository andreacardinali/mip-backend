from __future__ import annotations

from typing import Any

from market_screener.config import ScreenerRankingConfig
from market_screener.ranking import rank_opportunities
from market_screener.repository import MarketScreenerRepository


def build_market_screener_ranking(
    repository: MarketScreenerRepository,
    *,
    config: ScreenerRankingConfig,
    algorithm_version: str,
    signal_algorithm_version: str | None = None,
) -> dict[str, Any]:
    rows = repository.list_ranking_inputs(
        algorithm_version=signal_algorithm_version,
        limit=max(config.limit * 8, 200),
    )
    ranked = rank_opportunities(rows, config=config)
    payload = [
        {
            "signal_id": item.signal_id,
            "instrument_id": item.instrument_id,
            "symbol": item.symbol,
            "direction": item.direction,
            "origin": item.origin,
            "attention_score": item.attention_score,
            "technical_score": item.technical_score,
            "quant_score": item.quant_score,
            "llm_score": item.llm_score,
            "composite_score": item.composite_score,
            "ranking_position": item.ranking_position,
            "verdict": item.verdict,
            "summary": item.summary,
            "evidence": item.evidence,
            "strategy_count": item.strategy_count,
            "bullish_strategy_count": item.bullish_strategy_count,
            "bearish_strategy_count": item.bearish_strategy_count,
            "consensus_direction": item.consensus_direction,
            "consensus_score": item.consensus_score,
            "conflict_score": item.conflict_score,
            "supporting_strategies": list(item.supporting_strategies),
            "opposing_strategies": list(item.opposing_strategies),
            "supporting_signal_ids": list(item.supporting_signal_ids),
        }
        for item in ranked
    ]
    persisted = repository.replace_ranking(
        algorithm_version=algorithm_version,
        opportunities=payload,
    )

    # Keep repository test doubles and older compatible implementations valid.
    # There is nothing to enrich when the ranking payload is empty.
    apply_consensus = getattr(repository, "apply_consensus", None)
    consensus_persistence = None
    if payload and callable(apply_consensus):
        consensus_persistence = apply_consensus(
            algorithm_version=algorithm_version,
            opportunities=payload,
        )

    return {
        "status": "SUCCESS",
        "input_signals": len(rows),
        "ranked_opportunities": len(ranked),
        "algorithm_version": algorithm_version,
        "signal_algorithm_version": signal_algorithm_version,
        "symbols": [item.symbol for item in ranked],
        "persistence": persisted,
        "consensus_persistence": consensus_persistence,
    }
