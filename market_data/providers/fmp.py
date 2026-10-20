from urllib.error import HTTPError, URLError

from market_data.capabilities import MarketDataCapability, ProviderCapabilities
from market_data.exceptions import (
    CapabilityUnavailableError,
    InstrumentNotFoundError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)
from market_data.models import InstrumentProfile, InstrumentSearchResult
from providers.fmp import FMPProvider as LegacyFMPProvider


class FMPProvider:
    name = "fmp"
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                MarketDataCapability.SYMBOL_SEARCH,
                MarketDataCapability.PROFILE,
                MarketDataCapability.EXCHANGE,
            }
        )
    )

    def __init__(self):
        self.legacy = LegacyFMPProvider()

    def search(self, query, *, limit=20):
        try:
            rows = self.legacy.search_symbols(query, limit)
        except (HTTPError, URLError) as error:
            raise _normalize_transport_error(error) from error

        return [
            InstrumentSearchResult(
                symbol=row["symbol"],
                name=row.get("name") or row["symbol"],
                currency=row.get("currency"),
                exchange_code=row.get("exchange"),
                exchange_name=row.get("exchangeFullName"),
                source=self.name,
            )
            for row in rows
        ]

    def get_profile(self, instrument):
        if not instrument.symbol:
            raise InstrumentNotFoundError("FMP requires symbol")

        try:
            profile = self.legacy.get_profile(instrument.symbol)
        except (HTTPError, URLError) as error:
            raise _normalize_transport_error(error) from error

        if not profile:
            raise InstrumentNotFoundError(instrument.symbol)

        return InstrumentProfile(
            symbol=profile["symbol"],
            name=profile.get("name") or profile["symbol"],
            instrument_type=instrument.instrument_type,
            currency=profile.get("currency"),
            country_code=profile.get("country"),
            exchange_code=profile.get("exchange"),
            exchange_name=profile.get("exchangeFullName"),
            sector=profile.get("sector"),
            website=profile.get("website"),
            source=self.name,
        )


def _normalize_transport_error(error: HTTPError | URLError) -> Exception:
    if isinstance(error, URLError) and not isinstance(error, HTTPError):
        return ProviderTemporaryError(f"FMP transport error: {error.reason}")

    status_code = int(error.code)
    if status_code == 404:
        return InstrumentNotFoundError("FMP resource not found")
    if status_code == 429:
        return ProviderRateLimitError("FMP rate limit exceeded")
    if status_code == 402:
        return CapabilityUnavailableError(
            "FMP capability is not available for the configured subscription"
        )
    if status_code >= 500:
        return ProviderTemporaryError(f"FMP server error: HTTP {status_code}")
    return CapabilityUnavailableError(f"FMP request rejected: HTTP {status_code}")
