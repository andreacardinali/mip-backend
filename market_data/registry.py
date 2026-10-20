from market_data.base import ProviderPlugin


class ProviderRegistry:
    def __init__(self, providers: list[ProviderPlugin]) -> None:
        self._providers = {provider.name: provider for provider in providers}

    def get(self, name: str) -> ProviderPlugin:
        key = str(name).strip().lower()
        try:
            return self._providers[key]
        except KeyError as error:
            raise ValueError(f"Unknown market-data provider: {name}") from error

    def supporting(self, capability, *, instrument_type=None, interval=None):
        return [
            provider
            for provider in self._providers.values()
            if provider.capabilities.supports(
                capability, instrument_type=instrument_type, interval=interval
            )
        ]

    def describe(self) -> list[dict]:
        return [
            {"name": provider.name, "capabilities": provider.capabilities.to_dict()}
            for provider in self._providers.values()
        ]
