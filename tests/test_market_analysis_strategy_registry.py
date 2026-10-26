from datetime import UTC, datetime

from market_analysis.models import TechnicalSnapshot
from market_analysis.strategies import default_registry


def test_registry_emits_multiple_strategies():
    s = TechnicalSnapshot(
        100, 99, 1, 1, 25, 95, 90, 4, 2, 98, 1, 105, 85, 1, 1, 87, None, 10, 20
    )
    values = default_registry.evaluate(s, timeframe="1D", observed_at=datetime.now(UTC))
    codes = {x.strategy_code for x in values}
    assert (
        "SUPERTREND" in codes
        and "PULLBACK_DEEP_OVERSOLD" in codes
        and "SMC_BOS" in codes
    )
