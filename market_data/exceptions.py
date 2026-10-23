class MarketDataError(Exception):
    pass


class CapabilityUnavailableError(MarketDataError):
    pass


class InstrumentNotFoundError(MarketDataError):
    pass


class ProviderTemporaryError(MarketDataError):
    pass


class StaleMarketPriceError(ProviderTemporaryError):
    pass


class ProviderRateLimitError(ProviderTemporaryError):
    pass


class InvalidProviderResponseError(MarketDataError):
    pass


class AllProvidersFailedError(MarketDataError):
    def __init__(self, capability, failures):
        self.capability = capability
        self.failures = failures
        detail = "; ".join(f"{name}: {error}" for name, error in failures)
        super().__init__(f"No provider satisfied {capability}: {detail}")
