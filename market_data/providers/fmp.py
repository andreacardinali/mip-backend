import hashlib
import logging
import math
import time
from dataclasses import replace
from threading import Lock

from http_client import (
    RETRYABLE_EXCEPTIONS,
    HttpStatusError,
    RetryPolicy,
    create_http_client,
    request_with_retry,
)
from market_data.capabilities import MarketDataCapability, ProviderCapabilities
from market_data.exceptions import (
    CapabilityUnavailableError,
    InstrumentNotFoundError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)
from market_data.models import InstrumentProfile, InstrumentSearchResult

logger = logging.getLogger(__name__)


class FMPQuotaGuard:
    _disabled_until: dict[str, float] = {}
    _lock = Lock()

    def __init__(self, api_key, cooldown_seconds=86400):
        self.cooldown_seconds = float(cooldown_seconds)
        if not math.isfinite(self.cooldown_seconds) or self.cooldown_seconds < 0:
            raise ValueError("FMP cooldown_seconds must be finite and >= 0")
        self._key = hashlib.sha256(str(api_key or "").strip().encode()).hexdigest()

    def check_available(self):
        with self._lock:
            remaining = self._disabled_until.get(self._key, 0) - time.monotonic()
            if remaining <= 0:
                self._disabled_until.pop(self._key, None)
                return
        raise ProviderRateLimitError(
            f"FMP quota exhausted; provider suspended for another {remaining:.0f} seconds"
        )

    def apply_to_policy(self, policy):
        original_filter = policy.response_retry_filter

        def should_retry(response):
            return self.should_retry(response) and (
                original_filter is None or original_filter(response)
            )

        return replace(policy, response_retry_filter=should_retry)

    def should_retry(self, response):
        if response.status_code != 429:
            return True
        try:
            payload = response.json()
        except ValueError:
            return True
        message = payload.get("Error Message") if isinstance(payload, dict) else None
        if not isinstance(message, str) or "limit reach" not in message.casefold():
            return True
        with self._lock:
            self._disabled_until[self._key] = max(
                self._disabled_until.get(self._key, 0),
                time.monotonic() + self.cooldown_seconds,
            )
        logger.warning(
            "FMP quota exhausted; retries stopped and provider suspended for %.0f seconds",
            self.cooldown_seconds,
        )
        return False


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

    def __init__(
        self,
        api_key,
        *,
        base_url="https://financialmodelingprep.com",
        timeout=15.0,
        retry_policy=None,
        client=None,
        quota_cooldown_seconds=86400,
    ):
        self.api_key = str(api_key or "").strip()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._quota_guard = FMPQuotaGuard(self.api_key, quota_cooldown_seconds)
        self.retry_policy = self._quota_guard.apply_to_policy(retry_policy or RetryPolicy())
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
        self._quota_guard.check_available()
        owns_client = self._client is None
        client = self._client or create_http_client(timeout=self.timeout)
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
        except HttpStatusError as error:
            raise _normalize_http_error(error) from error
        except RETRYABLE_EXCEPTIONS as error:
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
