import math
from collections.abc import Sequence

from market_analysis.models import TechnicalSnapshot
from market_data.models import PriceBar


def mean(v):
    return sum(v) / len(v) if v else None


def sma(v, period):
    if period < 1:
        raise ValueError("SMA period must be >= 1")
    return mean(v[-period:]) if len(v) >= period else None


def rsi(closes, period=14):
    if period < 1:
        raise ValueError("RSI period must be >= 1")
    if len(closes) <= period:
        return None
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))][-period:]
    gain = mean([max(x, 0) for x in changes])
    loss = mean([max(-x, 0) for x in changes])
    if gain is None or loss is None:
        return None
    if loss == 0:
        return 100.0
    return 100 - (100 / (1 + gain / loss))


def bollinger_band_width(closes, period=20, standard_deviations=2.0):
    if len(closes) < period:
        return None
    w = closes[-period:]
    avg = mean(w)
    if not avg:
        return None
    sd = math.sqrt(mean([(x - avg) ** 2 for x in w]))
    return (
        (avg + standard_deviations * sd - (avg - standard_deviations * sd)) / avg
    ) * 100


def true_ranges(bars):
    out = []
    for i, b in enumerate(bars):
        prev = bars[i - 1].close if i else b.close
        out.append(max(b.high - b.low, abs(b.high - prev), abs(b.low - prev)))
    return out


def atr(bars, period=14):
    return mean(true_ranges(bars)[-period:]) if len(bars) >= period else None


def supertrend(bars, period=10, factor=3.0):
    if len(bars) < period + 1:
        return None, None
    trs = true_ranges(bars)
    atrs = []
    current = mean(trs[:period])
    atrs = [None] * (period - 1) + [current]
    for tr in trs[period:]:
        current = ((current * (period - 1)) + tr) / period
        atrs.append(current)
    upper = lower = trend = None
    direction = 1
    for i in range(period - 1, len(bars)):
        mid = (bars[i].high + bars[i].low) / 2
        a = atrs[i]
        basic_u = mid + factor * a
        basic_l = mid - factor * a
        prev_close = bars[i - 1].close if i else bars[i].close
        upper = (
            basic_u if upper is None or basic_u < upper or prev_close > upper else upper
        )
        lower = (
            basic_l if lower is None or basic_l > lower or prev_close < lower else lower
        )
        if trend is None:
            trend = lower
        elif direction == 1 and bars[i].close < lower:
            direction = -1
            trend = upper
        elif direction == -1 and bars[i].close > upper:
            direction = 1
            trend = lower
        else:
            trend = lower if direction == 1 else upper
    return trend, direction


def market_structure(bars, lookback=20):
    if len(bars) < lookback + 2:
        return (None, None, 0, 0, None, None)
    prior = bars[-lookback - 1 : -1]
    current = bars[-1]
    high = max(x.high for x in prior)
    low = min(x.low for x in prior)
    bos = 1 if current.close > high else -1 if current.close < low else 0
    recent_direction = 1 if bars[-2].close >= bars[-2].open else -1
    choch = -recent_direction if bos and bos != recent_direction else 0
    bull = (
        next((x.low for x in reversed(prior) if x.close < x.open), None)
        if bos == 1
        else None
    )
    bear = (
        next((x.high for x in reversed(prior) if x.close > x.open), None)
        if bos == -1
        else None
    )
    return high, low, choch, bos, bull, bear


def analyze_price_bars(bars: Sequence[PriceBar]):
    if len(bars) < 2:
        return None
    closes = [b.close for b in bars]
    cur = closes[-1]
    prev = closes[-2]
    high, low, choch, bos, bull, bear = market_structure(bars)
    st, st_dir = supertrend(bars)
    recent = bars[-min(len(bars), 252) :]
    period_high = max(x.high for x in recent)
    period_low = min(x.low for x in recent)
    return TechnicalSnapshot(
        cur,
        prev,
        (cur / prev - 1) * 100 if prev else 0,
        bars[-1].timestamp,
        rsi(closes),
        sma(closes, 50),
        sma(closes, 200),
        bollinger_band_width(closes),
        atr(bars),
        st,
        st_dir,
        high,
        low,
        choch,
        bos,
        bull,
        bear,
        (period_high - cur) / period_high * 100 if period_high else None,
        (cur - period_low) / period_low * 100 if period_low else None,
    )
