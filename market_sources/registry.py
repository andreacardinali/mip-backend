from market_sources.base import SourceProviderPlugin


class SourceProviderRegistry:
    def __init__(self, providers: list[SourceProviderPlugin]):
        self._providers = {p.name: p for p in providers}

    def get(self, name):
        try:
            return self._providers[str(name).strip().lower()]
        except KeyError as error:
            raise ValueError(f"Unknown market-source provider: {name}") from error

    def describe(self):
        return [
            {"name": p.name, "capabilities": p.capabilities.to_dict()}
            for p in self._providers.values()
        ]
