class MarketSourceError(Exception):
    pass


class SourceCapabilityUnavailableError(MarketSourceError):
    pass


class SourceNotFoundError(MarketSourceError):
    pass


class InvalidSourceResponseError(MarketSourceError):
    pass


class SourceRateLimitError(MarketSourceError):
    pass


class SourceTemporaryError(MarketSourceError):
    pass


class AllSourcesFailedError(MarketSourceError):
    def __init__(self, capability, failures):
        self.capability, self.failures = capability, failures
        details = "; ".join(f"{name}: {error}" for name, error in failures)
        super().__init__(f"All market sources failed for {capability}: {details}")
