import math
from collections.abc import Sequence

from market_analysis.models import TechnicalSnapshot
from market_data.models import PriceBar


def mean(values):
    return sum(values) / len(values) if values else None


def sma(values, period):
    if period < 1:
        raise ValueError("SMA period must be >= 1")
    return mean(values[-period:]) if len(values) >= period else None


def ema(values, period):
    if period < 1:
        raise ValueError("EMA period must be >= 1")
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    current = mean(values[:period])
    for value in values[period:]:
        current = alpha * value + (1 - alpha) * current
    return current


def rsi(closes, period=14):
    if period < 1:
        raise ValueError("RSI period must be >= 1")
    if len(closes) <= period:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))][
        -period:
    ]
    gain = mean([max(value, 0) for value in changes])
    loss = mean([max(-value, 0) for value in changes])
    if gain is None or loss is None:
        return None
    if loss == 0:
        return 100.0
    return 100 - (100 / (1 + gain / loss))


def bollinger_bands(closes, period=20, standard_deviations=2.0):
    if len(closes) < period:
        return None, None, None
    window = closes[-period:]
    average = mean(window)
    if not average:
        return None, None, None
    deviation = math.sqrt(mean([(value - average) ** 2 for value in window]))
    lower = average - standard_deviations * deviation
    upper = average + standard_deviations * deviation
    width = ((upper - lower) / average) * 100
    return lower, upper, width


def bollinger_band_width(closes, period=20, standard_deviations=2.0):
    """Backward-compatible public helper retained by market_analysis.__init__."""
    _, _, width = bollinger_bands(
        closes,
        period=period,
        standard_deviations=standard_deviations,
    )
    return width


def true_ranges(bars):
    output = []
    for index, bar in enumerate(bars):
        previous = bars[index - 1].close if index else bar.close
        output.append(
            max(bar.high - bar.low, abs(bar.high - previous), abs(bar.low - previous))
        )
    return output


def atr(bars, period=14):
    return mean(true_ranges(bars)[-period:]) if len(bars) >= period else None


def supertrend(bars, period=10, factor=3.0):
    if len(bars) < period + 1:
        return None, None
    ranges = true_ranges(bars)
    current_atr = mean(ranges[:period])
    atrs = [None] * (period - 1) + [current_atr]
    for true_range in ranges[period:]:
        current_atr = ((current_atr * (period - 1)) + true_range) / period
        atrs.append(current_atr)
    upper = lower = trend = None
    direction = 1
    for index in range(period - 1, len(bars)):
        middle = (bars[index].high + bars[index].low) / 2
        basic_upper = middle + factor * atrs[index]
        basic_lower = middle - factor * atrs[index]
        previous_close = bars[index - 1].close if index else bars[index].close
        upper = (
            basic_upper
            if upper is None or basic_upper < upper or previous_close > upper
            else upper
        )
        lower = (
            basic_lower
            if lower is None or basic_lower > lower or previous_close < lower
            else lower
        )
        if trend is None:
            trend = lower
        elif direction == 1 and bars[index].close < lower:
            direction, trend = -1, upper
        elif direction == -1 and bars[index].close > upper:
            direction, trend = 1, lower
        else:
            trend = lower if direction == 1 else upper
    return trend, direction


def market_structure(bars, lookback=20):
    if len(bars) < lookback + 2:
        return None, None, 0, 0, None, None
    prior = bars[-lookback - 1 : -1]
    current = bars[-1]
    high = max(value.high for value in prior)
    low = min(value.low for value in prior)
    bos = 1 if current.close > high else -1 if current.close < low else 0
    recent_direction = 1 if bars[-2].close >= bars[-2].open else -1
    choch = bos if bos and bos != recent_direction else 0
    bullish_block = (
        next((value.low for value in reversed(prior) if value.close < value.open), None)
        if bos == 1
        else None
    )
    bearish_block = (
        next(
            (value.high for value in reversed(prior) if value.close > value.open), None
        )
        if bos == -1
        else None
    )
    return high, low, choch, bos, bullish_block, bearish_block


def fair_value_gaps(bars):
    if len(bars) < 3:
        return None, None
    first, current = bars[-3], bars[-1]
    bullish = (
        first.high if first.high < current.low and current.close > first.high else None
    )
    bearish = (
        first.low if first.low > current.high and current.close < first.low else None
    )
    return bullish, bearish


def analyze_price_bars(bars: Sequence[PriceBar]):
    if len(bars) < 2:
        return None
    closes = [bar.close for bar in bars]
    current = closes[-1]
    previous = closes[-2]
    swing_high, swing_low, choch, bos, bullish_block, bearish_block = market_structure(
        bars
    )
    supertrend_value, supertrend_direction = supertrend(bars)
    bollinger_lower, bollinger_upper, bollinger_width = bollinger_bands(closes)
    bullish_gap, bearish_gap = fair_value_gaps(bars)
    recent = bars[-min(len(bars), 252) :]
    period_high = max(value.high for value in recent)
    period_low = min(value.low for value in recent)
    return TechnicalSnapshot(
        market_price=current,
        previous_close=previous,
        daily_change_pct=(current / previous - 1) * 100 if previous else 0,
        observed_timestamp=bars[-1].timestamp,
        rsi=rsi(closes),
        sma_50=sma(closes, 50),
        sma_200=sma(closes, 200),
        bollinger_band_width_pct=bollinger_width,
        atr=atr(bars),
        supertrend_value=supertrend_value,
        supertrend_direction=supertrend_direction,
        swing_high=swing_high,
        swing_low=swing_low,
        change_of_character=choch,
        break_of_structure=bos,
        bullish_order_block=bullish_block,
        bearish_order_block=bearish_block,
        drop_from_high_pct=(
            (period_high - current) / period_high * 100 if period_high else None
        ),
        rise_from_low_pct=(
            (current - period_low) / period_low * 100 if period_low else None
        ),
        ema_50=ema(closes, 50),
        bollinger_lower=bollinger_lower,
        bollinger_upper=bollinger_upper,
        bullish_fair_value_gap=bullish_gap,
        bearish_fair_value_gap=bearish_gap,
    )
