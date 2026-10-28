import logging

from market_sources.capabilities import MarketSourceCapability
from market_sources.exceptions import (
    AllSourcesFailedError,
    InvalidSourceResponseError,
    SourceCapabilityUnavailableError,
    SourceNotFoundError,
    SourceRateLimitError,
    SourceTemporaryError,
)

logger = logging.getLogger(__name__)


class MarketSourceService:
    def __init__(self, registry, config):
        self.registry, self.config = registry, config

    def capabilities(self):
        return self.registry.describe()

    def get_universe_members(self, universe_code):
        return self._first(
            MarketSourceCapability.UNIVERSE_MEMBERS,
            lambda p: p.get_universe_members(universe_code),
        )

    def get_universe_snapshot(self, universe_code):
        return self._first(
            MarketSourceCapability.UNIVERSE_MEMBERS,
            lambda p: p.get_universe_snapshot(universe_code),
        )

    def discover(self, request):
        return self._first(
            MarketSourceCapability.DISCOVERY, lambda p: p.discover(request)
        )

    def _ordered(self, capability):
        result = []
        for name in self.config.provider_orders.get(capability, ()):
            try:
                provider = self.registry.get(name)
            except ValueError:
                logger.warning(
                    "Configured market-source provider is not registered capability=%s provider=%s",
                    capability.value,
                    name,
                )
                continue
            if provider.capabilities.supports(capability):
                result.append(provider)
        return result

    def _first(self, capability, operation, required=True):
        failures = []
        for provider in self._ordered(capability):
            try:
                value = operation(provider)
                if value is not None and value != []:
                    return value
                failures.append((provider.name, SourceNotFoundError("empty result")))
            except (
                SourceCapabilityUnavailableError,
                SourceNotFoundError,
                InvalidSourceResponseError,
                SourceRateLimitError,
                SourceTemporaryError,
            ) as error:
                failures.append((provider.name, error))
            except Exception as error:
                failures.append((provider.name, error))
                logger.exception(
                    "Unexpected source failure capability=%s provider=%s",
                    capability.value,
                    provider.name,
                )
        if required:
            raise AllSourcesFailedError(capability.value, failures)
        return None
