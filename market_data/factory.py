from config.settings import settings
from http_client.retry import RetryPolicy
from market_data.config import MarketDataConfig
from market_data.providers.eulerpool import EulerpoolProvider
from market_data.providers.fmp import FMPProvider
from market_data.providers.ghostfolio import GhostfolioProvider
from market_data.providers.openfigi import OpenFigiProvider
from market_data.providers.yahoo import YahooMarketDataProvider
from market_data.registry import ProviderRegistry
from market_data.service import MarketDataService


def create_market_data_service(config=None):
    config = config or MarketDataConfig.from_settings(settings)
    retry = RetryPolicy(
        max_retries=config.http_max_retries,
        base_delay_seconds=config.http_retry_base_seconds,
    )
    providers = [
        YahooMarketDataProvider(timeout=config.http_timeout, retry_policy=retry),
        GhostfolioProvider(),
        FMPProvider(),
    ]
    euler_key = settings.get("EULERPOOL_API_KEY")
    if euler_key:
        providers.insert(
            0,
            EulerpoolProvider(
                euler_key, timeout=config.http_timeout, retry_policy=retry
            ),
        )
    providers.append(
        OpenFigiProvider(
            settings.get("OPENFIGI_API_KEY"),
            timeout=config.http_timeout,
            retry_policy=retry,
        )
    )
    return MarketDataService(ProviderRegistry(providers), config)


market_data = create_market_data_service()
