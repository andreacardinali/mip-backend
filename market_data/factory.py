from http_client.retry import RetryPolicy
from market_data.config import MarketDataConfig
from market_data.providers.yahoo import YahooMarketDataProvider
from market_data.service import MarketDataService


def create_market_data_service(config: MarketDataConfig) -> MarketDataService:
    config.validate()
    if config.provider == "yahoo":
        return MarketDataService(
            YahooMarketDataProvider(
                timeout=config.http_timeout,
                retry_policy=RetryPolicy(
                    max_retries=config.http_max_retries,
                    base_delay_seconds=config.http_retry_base_seconds,
                ),
            )
        )
    raise ValueError(f"Unsupported market-data provider: {config.provider}")
