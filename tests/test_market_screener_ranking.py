from market_screener.config import ScreenerRankingConfig
from market_screener.ranking import rank_opportunities


def test_rank_opportunities_uses_available_weighted_scores():
    rows = [
        {
            "signal_id": "1",
            "instrument_id": "i1",
            "symbol": "BBB",
            "direction": "LONG",
            "attention_score": 80,
            "technical_score": 60,
            "quant_score": 70,
            "llm_score": 90,
        },
        {
            "signal_id": "2",
            "instrument_id": "i2",
            "symbol": "AAA",
            "direction": "LONG",
            "attention_score": 60,
            "technical_score": 60,
            "quant_score": 60,
            "llm_score": 60,
        },
    ]
    ranked = rank_opportunities(rows, config=ScreenerRankingConfig())
    assert [item.symbol for item in ranked] == ["BBB", "AAA"]
    assert ranked[0].ranking_position == 1
    assert ranked[0].composite_score == 73.5


def test_missing_llm_score_reweights_available_components():
    rows = [
        {
            "signal_id": "1",
            "instrument_id": "i1",
            "symbol": "AAA",
            "direction": "LONG",
            "attention_score": 80,
            "technical_score": 60,
            "quant_score": 70,
            "llm_score": None,
        }
    ]
    ranked = rank_opportunities(rows, config=ScreenerRankingConfig())
    assert ranked[0].composite_score == 69.375
