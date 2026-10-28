import math
from collections.abc import Sequence
from dataclasses import dataclass

from market_analysis.models import TechnicalSnapshot
from market_data.models import PriceBar


@dataclass(frozen=True, slots=True)
class TechnicalConfig:
    supertrend_period: int = 10
    supertrend_factor: float = 3.0
    supertrend_use_rma: bool = True
    swing_length: int = 50
    order_block_lookback: int = 10
    fvg_threshold: float = 0.0001
    high_lookback: int = 126

    def __post_init__(self):
        if (
            min(
                self.supertrend_period,
                self.swing_length,
                self.order_block_lookback,
                self.high_lookback,
            )
            < 1
        ):
            raise ValueError("indicator periods must be positive")
        if not math.isfinite(self.supertrend_factor) or self.supertrend_factor <= 0:
            raise ValueError("supertrend factor must be finite and positive")
        if not math.isfinite(self.fvg_threshold) or self.fvg_threshold < 0:
            raise ValueError("FVG threshold must be finite and nonnegative")


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
    current = values[0]
    for value in values[1:]:
        current = alpha * value + (1 - alpha) * current
    return current


def rsi(closes, period=14):
    if period < 1:
        raise ValueError("RSI period must be >= 1")
    if len(closes) <= period:
        return None
    gain = loss = 0.0
    alpha = 1.0 / period
    for previous, current in zip(closes, closes[1:], strict=False):
        change = current - previous
        gain = alpha * max(change, 0) + (1 - alpha) * gain
        loss = alpha * max(-change, 0) + (1 - alpha) * loss
    if loss == 0:
        return 100.0 if gain else None
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


def supertrend(bars, period=10, factor=3.0, use_rma=True):
    if period < 1 or factor <= 0:
        raise ValueError("Supertrend period and factor must be positive")
    if len(bars) < 2 or (not use_rma and len(bars) < period):
        return None, None
    ranges = true_ranges(bars)
    current_atr = ranges[0]
    upper = (bars[0].high + bars[0].low) / 2 + factor * current_atr if use_rma else None
    lower = (bars[0].high + bars[0].low) / 2 - factor * current_atr if use_rma else None
    trend = None
    direction = 1
    for index in range(1, len(bars)):
        if use_rma:
            current_atr += (ranges[index] - current_atr) / period
        else:
            current_atr = sma(ranges[: index + 1], period)
            if current_atr is None:
                continue
        middle = (bars[index].high + bars[index].low) / 2
        basic_upper = middle + factor * current_atr
        basic_lower = middle - factor * current_atr
        previous_close = bars[index - 1].close
        previous_upper, previous_lower = upper, lower
        lower = (
            max(basic_lower, lower)
            if lower is not None and previous_close > lower
            else basic_lower
        )
        upper = (
            min(basic_upper, upper)
            if upper is not None and previous_close < upper
            else basic_upper
        )
        if (
            direction == -1
            and previous_upper is not None
            and bars[index].close > previous_upper
        ):
            direction = 1
        elif (
            direction == 1
            and previous_lower is not None
            and bars[index].close < previous_lower
        ):
            direction = -1
        trend = lower if direction == 1 else upper
    return trend, direction


def structure_snapshot(bars, *, config: TechnicalConfig):
    high = low = bullish_bottom = bullish_top = bearish_bottom = bearish_top = None
    bias = choch = bos = 0
    for index, current in enumerate(bars):
        previous_high, previous_low = high, low
        if index >= config.swing_length:
            delayed = bars[index - config.swing_length]
            following = bars[index - config.swing_length + 1 : index + 1]
            if delayed.high > max(bar.high for bar in following):
                high = delayed.high
            if delayed.low < min(bar.low for bar in following):
                low = delayed.low
        choch = bos = 0
        if not index:
            continue
        previous = bars[index - 1]
        bullish_cross = (
            high is not None
            and current.close > high
            and not (previous_high is not None and previous.close > previous_high)
        )
        bearish_cross = (
            low is not None
            and current.close < low
            and not (previous_low is not None and previous.close < previous_low)
        )
        window = bars[max(0, index - config.order_block_lookback) : index + 1]
        if bullish_cross:
            choch, bos = (1, 0) if bias <= 0 else (0, 1)
            bias = 1
            block = min(window, key=lambda bar: bar.low)
            bullish_bottom, bullish_top = block.low, block.high
        elif bearish_cross:
            choch, bos = (-1, 0) if bias >= 0 else (0, -1)
            bias = -1
            block = max(window, key=lambda bar: bar.high)
            bearish_bottom, bearish_top = block.low, block.high
    return {
        "swing_high": high,
        "swing_low": low,
        "change_of_character": choch,
        "break_of_structure": bos,
        "smc_bias": bias,
        "bullish_order_block": bullish_bottom,
        "bullish_order_block_top": bullish_top,
        "bearish_order_block": bearish_bottom,
        "bearish_order_block_top": bearish_top,
    }


def market_structure(bars, lookback=50):
    result = structure_snapshot(bars, config=TechnicalConfig(swing_length=lookback))
    return tuple(
        result[name]
        for name in (
            "swing_high",
            "swing_low",
            "change_of_character",
            "break_of_structure",
            "bullish_order_block",
            "bearish_order_block",
        )
    )


def gap_snapshot(bars, threshold=0.0001):
    bullish_bottom = bullish_top = bearish_bottom = bearish_top = None
    for index in range(2, len(bars)):
        first, current = bars[index - 2], bars[index]
        delta = (current.close - current.open) / current.open
        if (
            first.high < current.low
            and current.close > first.high
            and delta > threshold
        ):
            bullish_bottom, bullish_top = first.high, current.low
        if (
            first.low > current.high
            and current.close < first.low
            and -delta > threshold
        ):
            bearish_bottom, bearish_top = current.high, first.low
    return {
        "bullish_fair_value_gap": bullish_bottom,
        "bullish_fair_value_gap_top": bullish_top,
        "bearish_fair_value_gap": bearish_bottom,
        "bearish_fair_value_gap_top": bearish_top,
    }


def fair_value_gaps(bars):
    result = gap_snapshot(bars)
    return result["bullish_fair_value_gap"], result["bearish_fair_value_gap"]


def analyze_price_bars(
    bars: Sequence[PriceBar], *, config: TechnicalConfig | None = None
):
    if len(bars) < 2:
        return None
    config = config or TechnicalConfig()
    bars = sorted(bars, key=lambda bar: bar.timestamp)
    if len({bar.timestamp for bar in bars}) != len(bars):
        raise ValueError("duplicate price bar timestamps")
    for bar in bars:
        if (
            not all(
                math.isfinite(value)
                for value in (bar.open, bar.high, bar.low, bar.close)
            )
            or bar.open <= 0
            or bar.close <= 0
            or bar.high < bar.low
        ):
            raise ValueError("invalid OHLC price bar")
    closes = [bar.close for bar in bars]
    current = closes[-1]
    previous = closes[-2]
    structure = structure_snapshot(bars, config=config)
    supertrend_value, supertrend_direction = supertrend(
        bars,
        config.supertrend_period,
        config.supertrend_factor,
        config.supertrend_use_rma,
    )
    bollinger_lower, bollinger_upper, bollinger_width = bollinger_bands(closes)
    gaps = gap_snapshot(bars, config.fvg_threshold)
    recent = bars[-config.high_lookback :]
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
        **structure,
        drop_from_high_pct=(
            (period_high - current) / period_high * 100 if period_high else None
        ),
        rise_from_low_pct=(
            (current - period_low) / period_low * 100 if period_low else None
        ),
        ema_50=ema(closes, 50),
        bollinger_lower=bollinger_lower,
        bollinger_upper=bollinger_upper,
        **gaps,
    )
