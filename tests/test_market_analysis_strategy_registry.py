from datetime import UTC, datetime

import pytest

from market_analysis.models import SignalDirection, TechnicalSnapshot
from market_analysis.strategies import StrategyRegistry
from market_analysis.strategies.deep_oversold import DeepOversoldStrategy
from market_analysis.strategies.smart_money_structure import SmcBosStrategy
from market_analysis.strategies.supertrend import SupertrendStrategy

NOW = datetime(2026, 10, 26, tzinfo=UTC)


def snapshot(**overrides):
    values = {
        "market_price": 105.0,
        "previous_close": 104.0,
        "daily_change_pct": 0.96,
        "observed_timestamp": 1_700_000_000,
        "rsi": 45.0,
        "sma_50": 100.0,
        "sma_200": 90.0,
        "bollinger_band_width_pct": 4.0,
        "atr": 2.0,
        "supertrend_value": 98.0,
        "supertrend_direction": 1,
        "ema_50": 101.0,
    }
    values.update(overrides)
    return TechnicalSnapshot(**values)


def test_supertrend_requires_price_alignment():
    strategy = SupertrendStrategy()
    assert (
        strategy.evaluate(snapshot(market_price=99), timeframe="1D", observed_at=NOW)
        is None
    )
    value = strategy.evaluate(snapshot(), timeframe="1D", observed_at=NOW)
    assert value is not None
    assert value.direction == SignalDirection.LONG
    assert value.invalidation_price == 98.0


def test_deep_oversold_requires_material_drawdown():
    strategy = DeepOversoldStrategy()
    assert (
        strategy.evaluate(
            snapshot(rsi=18, drop_from_high_pct=3), timeframe="1D", observed_at=NOW
        )
        is None
    )
    value = strategy.evaluate(
        snapshot(rsi=18, drop_from_high_pct=12, swing_low=80),
        timeframe="1D",
        observed_at=NOW,
    )
    assert value is not None
    assert value.strategy_code == "PULLBACK_DEEP_OVERSOLD"


def test_smc_bos_requires_breakout_and_confirmation():
    strategy = SmcBosStrategy()
    base = snapshot(
        market_price=110,
        swing_high=105,
        break_of_structure=1,
        bullish_order_block=100,
    )
    value = strategy.evaluate(base, timeframe="1D", observed_at=NOW)
    assert value is not None
    assert value.direction == SignalDirection.LONG
    assert value.evidence["strategy"]["confirmation"] == "order_block"


def test_registry_rejects_unknown_enabled_strategy():
    registry = StrategyRegistry((SupertrendStrategy(),))
    with pytest.raises(ValueError, match="unknown enabled strategies"):
        registry.evaluate(
            snapshot(), timeframe="1D", observed_at=NOW, enabled=("MISSING",)
        )
