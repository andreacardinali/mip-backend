from market_screener.config import ScreenerRankingConfig
from market_screener.consensus import build_strategy_consensus
from market_screener.ranking import rank_opportunities


def rows():
    return [
        {
            "signal_id": "1",
            "instrument_id": "i",
            "symbol": "ABC",
            "direction": "LONG",
            "strategy_code": "A",
            "signal_timeframe": "1D",
            "quant_score": 80,
            "technical_score": 70,
        },
        {
            "signal_id": "2",
            "instrument_id": "i",
            "symbol": "ABC",
            "direction": "SHORT",
            "strategy_code": "B",
            "signal_timeframe": "1D",
            "quant_score": 60,
            "technical_score": 70,
        },
    ]


def test_conflict_is_aggregated():
    value = build_strategy_consensus(rows())[0]
    assert (
        value["strategy_count"] == 2
        and value["consensus_direction"] == "CONFLICT"
        and value["conflict_score"] == 100
    )
    assert value["supporting_strategies"] == ["A"] and value["opposing_strategies"] == [
        "B"
    ]


def test_conflict_penalizes_ranking():
    ranked = rank_opportunities(rows(), config=ScreenerRankingConfig())
    assert (
        len(ranked) == 1
        and ranked[0].consensus_direction == "CONFLICT"
        and ranked[0].composite_score < 80
    )
