from market_data.capabilities import (
    InstrumentType,
    MarketDataCapability,
    ProviderCapabilities,
)
from market_data.providers.yahoo import YahooProvider


def test_yahoo_uses_shared_provider_capabilities_contract():
    capabilities = YahooProvider.capabilities
    assert isinstance(capabilities, ProviderCapabilities)
    assert capabilities.supports(
        MarketDataCapability.LIVE_PRICE,
        instrument_type=InstrumentType.UNKNOWN,
    )
    assert capabilities.supports(
        MarketDataCapability.PRICE_HISTORY,
        instrument_type=InstrumentType.STOCK,
        interval="1d",
    )
    assert capabilities.supports(
        MarketDataCapability.OHLCV,
        instrument_type=InstrumentType.ETF,
        interval="1wk",
    )
    assert "live_price" in capabilities.to_dict()
