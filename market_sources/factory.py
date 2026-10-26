from config.settings import settings
from http_client.retry import RetryPolicy
from market_sources.config import MarketSourceConfig
from market_sources.discovery.providers.apewisdom import ApeWisdomDiscoveryProvider
from market_sources.registry import SourceProviderRegistry
from market_sources.service import MarketSourceService
from market_sources.universe.providers.wikipedia import WikipediaUniverseProvider


def _retry_policy(config: MarketSourceConfig) -> RetryPolicy:
    return RetryPolicy(
        max_retries=config.http_max_retries,
        base_delay_seconds=config.http_retry_base_seconds,
    )


def create_market_source_service(config=None):
    config = config or MarketSourceConfig.from_settings(settings)
    retry_policy = _retry_policy(config)
    providers = [
        WikipediaUniverseProvider(
            user_agent=str(
                settings.get(
                    "WIKIPEDIA_USER_AGENT",
                    "MIP/1.0 (https://github.com/andreacardinali/mip-backend)",
                )
            ),
            timeout=float(settings.get("WIKIPEDIA_HTTP_TIMEOUT", config.http_timeout)),
            retry_policy=retry_policy,
        ),
        ApeWisdomDiscoveryProvider(
            user_agent=str(
                settings.get(
                    "APEWISDOM_USER_AGENT",
                    "MIP/1.0 (https://github.com/andreacardinali/mip-backend)",
                )
            ),
            timeout=float(settings.get("APEWISDOM_HTTP_TIMEOUT", config.http_timeout)),
            retry_policy=retry_policy,
        ),
    ]
    return MarketSourceService(SourceProviderRegistry(providers), config)


market_sources = create_market_source_service()
