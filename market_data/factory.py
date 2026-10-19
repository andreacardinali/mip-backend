from market_data.config import MarketDataConfig
from market_data.providers.yahoo import YahooMarketDataProvider
from market_data.service import MarketDataService


def create_market_data_service(
    config: MarketDataConfig,
) -> MarketDataService:
    config.validate()

    if config.provider == "yahoo":
        provider = YahooMarketDataProvider(
            timeout=config.http_timeout,
        )
        return MarketDataService(provider)

    raise ValueError(
        f"Unsupported market-data provider: {config.provider}"
    )
