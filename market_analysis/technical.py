import math
from collections.abc import Sequence

from market_analysis.models import TechnicalSnapshot
from market_data.models import PriceBar


def mean(values: Sequence[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def sma(
    values: Sequence[float],
    period: int,
) -> float | None:
    if period < 1:
        raise ValueError("SMA period must be >= 1")
    if len(values) < period:
        return None
    return mean(values[-period:])


def rsi(
    closes: Sequence[float],
    period: int = 14,
) -> float | None:
    if period < 1:
        raise ValueError("RSI period must be >= 1")
    if len(closes) <= period:
        return None

    changes = [closes[index] - closes[index - 1] for index in range(1, len(closes))][
        -period:
    ]
    average_gain = mean([max(change, 0.0) for change in changes])
    average_loss = mean([max(-change, 0.0) for change in changes])

    if average_gain is None or average_loss is None:
        return None
    if average_loss == 0:
        return 100.0

    relative_strength = average_gain / average_loss
    return 100.0 - (100.0 / (1.0 + relative_strength))


def bollinger_band_width(
    closes: Sequence[float],
    *,
    period: int = 20,
    standard_deviations: float = 2.0,
) -> float | None:
    if period < 2:
        raise ValueError("Bollinger period must be >= 2")
    if standard_deviations <= 0:
        raise ValueError("Bollinger standard deviations must be > 0")
    if len(closes) < period:
        return None

    window = closes[-period:]
    average = mean(window)
    if average is None or average == 0:
        return None

    variance = mean([(value - average) ** 2 for value in window])
    if variance is None:
        return None

    standard_deviation = math.sqrt(variance)
    upper_band = average + standard_deviations * standard_deviation
    lower_band = average - standard_deviations * standard_deviation
    return ((upper_band - lower_band) / average) * 100.0


def analyze_price_bars(
    bars: Sequence[PriceBar],
) -> TechnicalSnapshot | None:
    if len(bars) < 2:
        return None

    closes = [bar.close for bar in bars]
    current = closes[-1]
    previous = closes[-2]
    daily_change_pct = (current / previous - 1.0) * 100.0 if previous != 0 else 0.0

    return TechnicalSnapshot(
        market_price=current,
        previous_close=previous,
        daily_change_pct=daily_change_pct,
        observed_timestamp=bars[-1].timestamp,
        rsi=rsi(closes),
        sma_50=sma(closes, 50),
        sma_200=sma(closes, 200),
        bollinger_band_width_pct=bollinger_band_width(closes),
    )
