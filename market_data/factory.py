from config.settings import settings
from http_client.retry import RetryPolicy
from market_data.config import MarketDataConfig
from market_data.providers.ecb import EcbProvider
from market_data.providers.eulerpool import EulerpoolProvider
from market_data.providers.fmp import FMPProvider
from market_data.providers.ghostfolio import GhostfolioProvider
from market_data.providers.openfigi import OpenFigiProvider
from market_data.providers.yahoo import YahooProvider
from market_data.registry import ProviderRegistry
from market_data.service import MarketDataService


def _retry_policy(c):
    return RetryPolicy(
        max_retries=c.http_max_retries, base_delay_seconds=c.http_retry_base_seconds
    )


def _ecb_retry_policy(c):
    return RetryPolicy(
        max_retries=int(
            settings.get("EXCHANGE_RATES_HTTP_MAX_RETRIES", c.http_max_retries)
        ),
        base_delay_seconds=float(
            settings.get(
                "EXCHANGE_RATES_HTTP_RETRY_BASE_SECONDS", c.http_retry_base_seconds
            )
        ),
    )


def create_market_data_service(config=None):
    config = config or MarketDataConfig.from_settings(settings)
    retry = _retry_policy(config)
    providers = [
        YahooProvider(timeout=config.http_timeout, retry_policy=retry),
        EcbProvider(
            timeout=float(
                settings.get("EXCHANGE_RATES_HTTP_TIMEOUT", config.http_timeout)
            ),
            retry_policy=_ecb_retry_policy(config),
        ),
    ]
    base, token = settings.get("GHOSTFOLIO_BASE_URL"), settings.get(
        "GHOSTFOLIO_SECURITY_TOKEN"
    )
    if base and token:
        providers.append(
            GhostfolioProvider(
                base, token, timeout=config.http_timeout, retry_policy=retry
            )
        )
    key = settings.get("FMP_API_KEY")
    if key:
        providers.append(
            FMPProvider(key, timeout=config.http_timeout, retry_policy=retry)
        )
    key = settings.get("EULERPOOL_API_KEY")
    if key:
        providers.insert(
            0, EulerpoolProvider(key, timeout=config.http_timeout, retry_policy=retry)
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
