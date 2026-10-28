import math

import pytest

from market_analysis.technical import TechnicalConfig, analyze_price_bars, atr, ema, rsi, supertrend
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
    assert result.swing_high is None
    assert result.swing_low == pytest.approx(117.9)
    assert "dropFromHighPct" in result.to_dict()


def test_indicators_are_deterministic():
    assert atr(bars(), 14) == atr(bars(), 14)
    assert supertrend(bars()) == supertrend(bars())


def test_ema_uses_first_value_seed():
    alpha = 2 / 51
    expected = 50 - (1 - alpha) * (1 - (1 - alpha) ** 49) / alpha
    assert ema(list(range(1, 51)), 50) == pytest.approx(expected)


def test_rsi_uses_wilder_history_not_last_window():
    closes = [100, 120] + [119 - index for index in range(20)]
    assert 20 < rsi(closes) < 40
    assert rsi([100] * 30) is None


def test_snapshot_is_order_independent_and_uses_126_bar_high():
    values = bars()
    values[0] = PriceBar(timestamp=0, open=100, high=1000, low=99, close=101, volume=1)
    result = analyze_price_bars(values)
    assert result == analyze_price_bars(list(reversed(values)))
    assert result.drop_from_high_pct < 5
    assert result.to_dict()["ema50"] is not None
    assert result.to_dict()["bbUpper"] is not None


def test_duplicate_and_nonfinite_bars_are_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        analyze_price_bars([bars()[0], bars()[0]])
    values = bars(2)
    values[1] = PriceBar(timestamp=1, open=100, high=101, low=99, close=math.nan, volume=1)
    with pytest.raises(ValueError, match="invalid"):
        analyze_price_bars(values)


def test_delayed_swing_and_persistent_gap_ranges():
    values = [PriceBar(timestamp=index, open=100, high=101, low=99, close=100, volume=1) for index in range(60)]
    values[0] = PriceBar(timestamp=0, open=100, high=120, low=80, close=100, volume=1)
    result = analyze_price_bars(values)
    assert result.swing_high == 120
    assert result.swing_low == 80
    values[54] = PriceBar(timestamp=54, open=122, high=126, low=121, close=125, volume=1)
    result = analyze_price_bars(values)
    assert result.bullish_order_block is not None
    assert result.bullish_fair_value_gap == 101
    assert result.bullish_fair_value_gap_top == 121
    assert result.change_of_character == 0
    assert result.smc_bias == 1
    assert TechnicalConfig().high_lookback == 126
