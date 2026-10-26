from market_analysis.technical import analyze_price_bars, atr, supertrend
from market_data.models import PriceBar


def bars(n=240):
    return [
        PriceBar(
            timestamp=i,
            open=100 + i * 0.1,
            high=102 + i * 0.1,
            low=99 + i * 0.1,
            close=101 + i * 0.1,
            volume=1000,
        )
        for i in range(n)
    ]


def test_technical_v2_fields():
    result = analyze_price_bars(bars())
    assert result.atr is not None
    assert result.supertrend_direction in (-1, 1)
    assert result.swing_high is not None and result.swing_low is not None
    assert "dropFromHighPct" in result.to_dict()


def test_indicators_are_deterministic():
    assert atr(bars(), 14) == atr(bars(), 14)
    assert supertrend(bars()) == supertrend(bars())
