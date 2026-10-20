import re
import time
from datetime import datetime

import httpx

from http_client.retry import RetryPolicy, request_with_retry
from market_data.capabilities import (
    InstrumentType,
    MarketDataCapability,
    ProviderCapabilities,
)
from market_data.exceptions import (
    CapabilityUnavailableError,
    InstrumentNotFoundError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)
from market_data.models import InstrumentProfile, InstrumentSearchResult, MarketQuote


class GhostfolioProvider:
    name = "ghostfolio"
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                MarketDataCapability.SYMBOL_SEARCH,
                MarketDataCapability.PROFILE,
                MarketDataCapability.IDENTIFIERS,
                MarketDataCapability.LIVE_PRICE,
            }
        )
    )

    def __init__(
        self,
        base_url,
        security_token,
        *,
        timeout=20.0,
        retry_policy=None,
        client=None,
    ):
        self.base_url = str(base_url or "").rstrip("/")
        self.security_token = str(security_token or "")
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client
        self._auth_token = None

    def search(self, query, *, limit=20):
        payload = self._request_json(
            "GET",
            "/api/v1/symbol/lookup",
            params={"query": query},
        )
        items = payload.get("items") if isinstance(payload, dict) else []
        return [
            InstrumentSearchResult(
                symbol=str(row.get("symbol") or ""),
                name=str(row.get("name") or row.get("symbol") or ""),
                instrument_type=_kind(row.get("assetSubClass")),
                currency=row.get("currency"),
                source=self.name,
            )
            for row in (items or [])[:limit]
            if isinstance(row, dict) and row.get("symbol")
        ]

    def get_profile(self, instrument):
        if not instrument.symbol:
            raise InstrumentNotFoundError("Ghostfolio requires symbol")
        data_source = "YAHOO"
        payload = self._request_json(
            "GET",
            "/api/v1/asset-profiles/"
            + _quote(data_source)
            + "/"
            + _quote(instrument.symbol),
        )
        profile = payload.get("assetProfile") if isinstance(payload, dict) else None
        if not isinstance(profile, dict) or not profile:
            raise InstrumentNotFoundError(instrument.symbol)
        price, timestamp = _latest_market_price(payload.get("marketData"))
        return InstrumentProfile(
            symbol=profile.get("symbol") or instrument.symbol,
            name=profile.get("name") or instrument.symbol,
            instrument_type=_kind(profile.get("assetSubClass")),
            currency=profile.get("currency") or instrument.currency,
            country_code=_weighted(profile.get("countries"), "code", "name"),
            figi=profile.get("figi"),
            figi_composite=profile.get("figiComposite"),
            figi_share_class=profile.get("figiShareClass"),
            sector=_weighted(profile.get("sectors"), "name", "sector"),
            website=_clean_url(profile.get("url")),
            last_price=price,
            price_timestamp=timestamp,
            source=self.name,
        )

    def get_quote(self, instrument):
        profile = self.get_profile(instrument)
        if profile.last_price is None:
            raise InstrumentNotFoundError(instrument.identifier)
        return MarketQuote(
            timestamp=profile.price_timestamp or int(time.time()),
            price=profile.last_price,
            currency=profile.currency,
            source=self.name,
        )

    def refresh_profile(self, instrument, *, data_source="YAHOO"):
        if not instrument.symbol:
            raise InstrumentNotFoundError("Ghostfolio requires symbol")
        self._request(
            "POST",
            "/api/v1/admin/gather/profile-data/"
            + _quote(data_source)
            + "/"
            + _quote(instrument.symbol),
            retry_non_idempotent=False,
        )

    def _authenticate(self, client):
        if self._auth_token:
            return
        try:
            response = request_with_retry(
                client,
                "POST",
                self.base_url + "/api/v1/auth/anonymous",
                policy=self.retry_policy,
                retry_non_idempotent=True,
                json={"accessToken": self.security_token},
            )
            payload = response.json()
        except httpx.HTTPStatusError as error:
            raise _normalize_http_error(error) from error
        token = payload.get("authToken") if isinstance(payload, dict) else None
        if not token:
            raise ProviderTemporaryError("Ghostfolio authentication returned no token")
        self._auth_token = str(token)

    def _request_json(self, method, path, **kwargs):
        response = self._request(method, path, **kwargs)
        try:
            return response.json()
        except ValueError as error:
            raise ProviderTemporaryError("Ghostfolio returned invalid JSON") from error

    def _request(self, method, path, *, retry_non_idempotent=False, **kwargs):
        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout)
        try:
            self._authenticate(client)
            for auth_attempt in range(2):
                headers = {**kwargs.pop("headers", {})}
                headers["Authorization"] = "Bearer " + self._auth_token
                try:
                    return request_with_retry(
                        client,
                        method,
                        self.base_url + path,
                        policy=self.retry_policy,
                        retry_non_idempotent=retry_non_idempotent,
                        headers=headers,
                        **kwargs,
                    )
                except httpx.HTTPStatusError as error:
                    if error.response.status_code == 401 and auth_attempt == 0:
                        self._auth_token = None
                        self._authenticate(client)
                        continue
                    raise _normalize_http_error(error) from error
            raise ProviderTemporaryError("Ghostfolio authentication retry failed")
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.PoolTimeout,
            httpx.RemoteProtocolError,
        ) as error:
            raise ProviderTemporaryError(
                f"Ghostfolio transport error: {error}"
            ) from error
        finally:
            if owns_client:
                client.close()


def _normalize_http_error(error):
    status_code = error.response.status_code
    if status_code == 404:
        return InstrumentNotFoundError("Ghostfolio resource not found")
    if status_code == 429:
        return ProviderRateLimitError("Ghostfolio rate limit exceeded")
    if status_code >= 500:
        return ProviderTemporaryError(f"Ghostfolio server error: HTTP {status_code}")
    return CapabilityUnavailableError(
        f"Ghostfolio request rejected: HTTP {status_code}"
    )


def _quote(value):
    from urllib.parse import quote

    return quote(str(value), safe="")


def _clean_url(value):
    if not value:
        return None
    match = re.search(r'href="([^"]+)"', str(value))
    return match.group(1) if match else str(value)


def _weighted(values, *keys):
    if not values:
        return None
    if isinstance(values, list):
        rows = [item for item in values if isinstance(item, dict)]
        if not rows:
            return None
        selected = max(rows, key=lambda item: item.get("weight") or 0)
        return next((selected.get(key) for key in keys if selected.get(key)), None)
    if isinstance(values, dict):
        return next((values.get(key) for key in keys if values.get(key)), None)
    return None


def _latest_market_price(value):
    rows = list(value.values()) if isinstance(value, dict) else value
    rows = [row for row in (rows or []) if isinstance(row, dict)]
    rows.sort(key=lambda row: row.get("date") or "")
    for row in reversed(rows):
        if row.get("marketPrice") is not None:
            timestamp = None
            date_value = row.get("date")
            if date_value:
                try:
                    timestamp = int(
                        datetime.fromisoformat(
                            str(date_value).replace("Z", "+00:00")
                        ).timestamp()
                    )
                except (TypeError, ValueError):
                    timestamp = None
            return float(row["marketPrice"]), timestamp
    return None, None


def _kind(value):
    return {
        "ETF": InstrumentType.ETF,
        "ETP": InstrumentType.ETP,
        "INDEX": InstrumentType.INDEX,
        "CRYPTOCURRENCY": InstrumentType.CRYPTO,
    }.get(str(value or "").upper(), InstrumentType.STOCK)
