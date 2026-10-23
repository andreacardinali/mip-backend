import httpx

from http_client.retry import RetryPolicy, request_with_retry
from market_data.capabilities import MarketDataCapability, ProviderCapabilities
from market_data.exceptions import (
    CapabilityUnavailableError,
    InstrumentNotFoundError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)
from market_data.models import InstrumentProfile, InstrumentSearchResult
from market_data.providers.base import MarketDataProvider


class FMPProvider(MarketDataProvider):
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

    def __init__(
        self,
        api_key,
        *,
        base_url="https://financialmodelingprep.com",
        timeout=15.0,
        retry_policy=None,
        client=None,
    ):
        self.api_key = str(api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client

    def search(self, query, *, limit=20):
        payload = self._get(
            "/stable/search-symbol",
            params={"query": query, "limit": limit},
        )
        if not isinstance(payload, list):
            return []
        return [
            InstrumentSearchResult(
                symbol=str(row.get("symbol") or ""),
                name=str(row.get("name") or row.get("symbol") or ""),
                currency=row.get("currency"),
                exchange_code=row.get("exchange"),
                exchange_name=row.get("exchangeFullName"),
                source=self.name,
            )
            for row in payload[:limit]
            if isinstance(row, dict) and row.get("symbol")
        ]

    def get_profile(self, instrument):
        if not instrument.symbol:
            raise InstrumentNotFoundError("FMP requires symbol")
        payload = self._get(
            "/stable/profile",
            params={"symbol": instrument.symbol},
        )
        if not isinstance(payload, list) or not payload:
            raise InstrumentNotFoundError(instrument.symbol)
        row = payload[0]
        if not isinstance(row, dict):
            raise ProviderTemporaryError("FMP returned an invalid profile payload")
        return InstrumentProfile(
            symbol=row.get("symbol") or instrument.symbol,
            name=row.get("companyName") or row.get("name") or instrument.symbol,
            instrument_type=instrument.instrument_type,
            currency=row.get("currency") or instrument.currency,
            country_code=row.get("country"),
            exchange_code=row.get("exchange"),
            exchange_name=row.get("exchangeFullName"),
            sector=row.get("sector"),
            website=row.get("website"),
            description=row.get("description"),
            source=self.name,
        )

    def _get(self, path, *, params=None):
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout)
        query = {**(params or {}), "apikey": self.api_key}
        try:
            response = request_with_retry(
                client,
                "GET",
                self.base_url + path,
                policy=self.retry_policy,
                params=query,
            )
            return response.json()
        except httpx.HTTPStatusError as error:
            raise _normalize_http_error(error) from error
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
        ) as error:
            raise ProviderTemporaryError(f"FMP transport error: {error}") from error
        finally:
            if owns_client:
                client.close()


def _normalize_http_error(error):
    status_code = error.response.status_code
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
