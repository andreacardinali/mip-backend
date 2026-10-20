import json
import re
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError

from config.settings import settings


class GhostfolioProfileNotFoundError(Exception):
    pass


class GhostfolioProvider:

    @staticmethod
    def _clean_url(value):
        if not value:
            return None
        match = re.search(r'href="([^"]+)"', value)
        return match.group(1) if match else value

    @staticmethod
    def _extract_last_price(market_data):
        if isinstance(market_data, dict):
            market_data = list(market_data.values())
        if not isinstance(market_data, list):
            return None

        rows = [row for row in market_data if isinstance(row, dict)]
        rows.sort(key=lambda row: row.get("date") or "")

        for row in reversed(rows):
            market_price = row.get("marketPrice")
            if market_price is not None:
                return market_price
        return None

    @staticmethod
    def _extract_weighted_value(values, *keys):
        if not values:
            return None
        if isinstance(values, list):
            rows = [item for item in values if isinstance(item, dict)]
            if not rows:
                return None
            selected = max(rows, key=lambda item: item.get("weight") or 0)
            for key in keys:
                if selected.get(key):
                    return selected[key]
            return None
        if isinstance(values, dict):
            for key in keys:
                if values.get(key):
                    return values[key]
            if values:
                return max(values, key=lambda key: values.get(key) or 0)
        return None

    def __init__(self):
        self.base_url = settings.GHOSTFOLIO_BASE_URL
        self.security_token = settings.GHOSTFOLIO_SECURITY_TOKEN
        self.auth_token = None

    def _authenticate(self):
        if self.auth_token:
            return
        payload = json.dumps({"accessToken": self.security_token}).encode()
        request = urllib.request.Request(
            self.base_url + "/api/v1/auth/anonymous",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            self.auth_token = json.loads(response.read())["authToken"]

    def _headers(self):
        self._authenticate()
        return {"Authorization": "Bearer " + self.auth_token}

    def search_symbols(self, query: str, limit: int = 20):
        url = (
            self.base_url
            + "/api/v1/symbol/lookup?"
            + urllib.parse.urlencode({"query": query})
        )
        request = urllib.request.Request(url, headers=self._headers())
        with urllib.request.urlopen(request, timeout=15) as response:
            payload = json.loads(response.read())

        return [
            {
                "symbol": item.get("symbol"),
                "name": item.get("name"),
                "currency": item.get("currency"),
                "asset_class": item.get("assetClass"),
                "asset_sub_class": item.get("assetSubClass"),
                "data_source": item.get("dataSource"),
                "exchange": None,
                "exchangeFullName": None,
            }
            for item in payload.get("items", [])[:limit]
        ]

    def refresh_profile(self, symbol, data_source="YAHOO"):
        url = (
            self.base_url
            + "/api/v1/admin/gather/profile-data/"
            + urllib.parse.quote(data_source, safe="")
            + "/"
            + urllib.parse.quote(symbol, safe="")
        )
        request = urllib.request.Request(
            url,
            headers=self._headers(),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=30):
            return

    def get_profile(self, symbol: str, data_source: str = "YAHOO"):
        url = (
            self.base_url.rstrip("/")
            + "/api/v1/asset-profiles/"
            + urllib.parse.quote(data_source, safe="")
            + "/"
            + urllib.parse.quote(symbol, safe="")
        )
        request = urllib.request.Request(url, headers=self._headers(), method="GET")

        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                payload = json.loads(response.read())
        except HTTPError as error:
            if error.code == 404:
                raise GhostfolioProfileNotFoundError(
                    f"Profile not found for {symbol} ({data_source})"
                ) from error
            raise

        if not isinstance(payload, dict):
            raise ValueError("Invalid Ghostfolio payload")

        profile = payload.get("assetProfile")
        if not isinstance(profile, dict) or not profile:
            return None

        return {
            "symbol": profile.get("symbol") or symbol,
            "name": profile.get("name"),
            "currency": profile.get("currency"),
            "country_code": self._extract_weighted_value(
                profile.get("countries"), "code", "name"
            ),
            "sector": self._extract_weighted_value(
                profile.get("sectors"), "name", "sector"
            ),
            "website": self._clean_url(profile.get("url")),
            "asset_class": profile.get("assetClass"),
            "asset_sub_class": profile.get("assetSubClass"),
            "data_source": profile.get("dataSource") or data_source,
            "exchange": None,
            "exchangeFullName": None,
            "last_price": self._extract_last_price(payload.get("marketData") or []),
            "figi": profile.get("figi"),
            "figi_composite": profile.get("figiComposite"),
            "figi_share_class": profile.get("figiShareClass"),
        }

    def refresh_and_get_profile(
        self,
        symbol: str,
        data_source: str = "YAHOO",
        timeout_seconds: int = 20,
        require_price: bool = True,
    ):
        self.refresh_profile(symbol=symbol, data_source=data_source)
        time.sleep(1)
        deadline = time.monotonic() + timeout_seconds
        last_profile = None

        while time.monotonic() < deadline:
            try:
                last_profile = self.get_profile(
                    symbol=symbol,
                    data_source=data_source,
                )
                complete = (
                    last_profile
                    and last_profile.get("symbol")
                    and last_profile.get("name")
                )
                price_ready = (
                    (not require_price or last_profile.get("last_price") is not None)
                    if last_profile
                    else False
                )

                if complete and price_ready:
                    return last_profile
            except GhostfolioProfileNotFoundError:
                return None
            except Exception as error:
                print(
                    "GHOSTFOLIO PROFILE ERROR",
                    {
                        "symbol": symbol,
                        "data_source": data_source,
                        "error": repr(error),
                    },
                    flush=True,
                )
            time.sleep(1)

        print(
            "GHOSTFOLIO PROFILE TIMEOUT",
            {
                "symbol": symbol,
                "data_source": data_source,
                "timeout_seconds": timeout_seconds,
                "price_available": bool(
                    last_profile and last_profile.get("last_price") is not None
                ),
            },
            flush=True,
        )
        return last_profile
