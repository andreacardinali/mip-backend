from market_screener.config import ScreenerRankingConfig
from market_screener.ranking import rank_opportunities


def test_single_strategy_keeps_legacy_weighted_score() -> None:
    ranked = rank_opportunities(
        [
            {
                "signal_id": "1",
                "instrument_id": "i1",
                "symbol": "ABC",
                "direction": "LONG",
                "attention_score": 80,
                "technical_score": 60,
                "quant_score": 70,
                "llm_score": 90,
            }
        ],
        config=ScreenerRankingConfig(),
    )

    assert ranked[0].strategy_count == 1
    assert ranked[0].composite_score == 73.5


def test_two_aligned_strategies_receive_consensus_bonus() -> None:
    ranked = rank_opportunities(
        [
            {
                "signal_id": "1",
                "instrument_id": "i1",
                "symbol": "ABC",
                "direction": "LONG",
                "strategy_code": "TREND_PULLBACK",
                "signal_timeframe": "1D",
                "attention_score": 80,
                "technical_score": 60,
                "quant_score": 70,
                "llm_score": 90,
            },
            {
                "signal_id": "2",
                "instrument_id": "i1",
                "symbol": "ABC",
                "direction": "LONG",
                "strategy_code": "SUPERTREND",
                "signal_timeframe": "1D",
                "attention_score": 80,
                "technical_score": 60,
                "quant_score": 65,
                "llm_score": 90,
            },
        ],
        config=ScreenerRankingConfig(),
    )

    assert ranked[0].strategy_count == 2
    assert ranked[0].consensus_direction == "LONG"
    assert ranked[0].composite_score == 81.5
