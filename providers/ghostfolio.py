import json
import os
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

        match = re.search(
            r'href="([^"]+)"',
            value
        )

        if match:
            return match.group(1)

        return value

    @staticmethod
    def _extract_last_price(
        market_data,
    ):
        if isinstance(market_data, dict):
            market_data = list(
                market_data.values()
            )

        if not isinstance(market_data, list):
            return None

        valid_rows = [
            row
            for row in market_data
            if isinstance(row, dict)
        ]

        valid_rows.sort(
            key=lambda row: (
                row.get("date") or ""
            )
        )

        for row in reversed(valid_rows):
            market_price = row.get(
                "marketPrice"
            )

            if market_price is not None:
                return market_price

        return None

    @staticmethod
    def _extract_country_code(
        countries,
    ):
        if not countries:
            return None

        if isinstance(countries, list):
            valid_countries = [
                item
                for item in countries
                if isinstance(item, dict)
            ]

            if not valid_countries:
                return None

            main_country = max(
                valid_countries,
                key=lambda item: (
                    item.get("weight") or 0
                ),
            )

            return (
                main_country.get("code")
                or main_country.get("name")
            )

        if isinstance(countries, dict):
            if "code" in countries:
                return countries.get("code")

            if not countries:
                return None

            return max(
                countries,
                key=lambda key: (
                    countries.get(key) or 0
                ),
            )

        return None

    @staticmethod
    def _extract_country_code(
        countries,
    ):
        if not countries:
            return None

        if isinstance(countries, list):
            valid_countries = [
                item
                for item in countries
                if isinstance(item, dict)
            ]

            if not valid_countries:
                return None

            main_country = max(
                valid_countries,
                key=lambda item: (
                    item.get("weight") or 0
                ),
            )

            return (
                main_country.get("code")
                or main_country.get("name")
            )

        if isinstance(countries, dict):
            if "code" in countries:
                return countries.get("code")

            if not countries:
                return None

            return max(
                countries,
                key=lambda key: (
                    countries.get(key) or 0
                ),
            )

        return None

    @staticmethod
    def _extract_sector(
        sectors,
    ):
        if not sectors:
            return None

        if isinstance(sectors, list):
            valid_sectors = [
                item
                for item in sectors
                if isinstance(item, dict)
            ]

            if not valid_sectors:
                return None

            main_sector = max(
                valid_sectors,
                key=lambda item: (
                    item.get("weight") or 0
                ),
            )

            return (
                main_sector.get("name")
                or main_sector.get("sector")
            )

        if isinstance(sectors, dict):
            if "name" in sectors:
                return sectors.get("name")

            if not sectors:
                return None

            return max(
                sectors,
                key=lambda key: (
                    sectors.get(key) or 0
                ),
            )

        return None

    def __init__(self):

        self.base_url = settings.GHOSTFOLIO_BASE_URL

        self.security_token = settings.GHOSTFOLIO_SECURITY_TOKEN

        self.auth_token = None

    def _authenticate(self):

        if self.auth_token:
            return

        payload = json.dumps(
            {
                "accessToken":
                    self.security_token
            }
        ).encode()

        request = urllib.request.Request(
            self.base_url
            + "/api/v1/auth/anonymous",
            data=payload,
            headers={
                "Content-Type":
                    "application/json"
            },
            method="POST"
        )

        with urllib.request.urlopen(
            request,
            timeout=10
        ) as response:

            data = json.loads(
                response.read()
            )

        self.auth_token = (
            data["authToken"]
        )

    def _headers(self):

        self._authenticate()

        return {
            "Authorization":
                (
                    "Bearer "
                    + self.auth_token
                )
        }

    def search_symbols(
        self,
        query: str,
        limit: int = 20
    ):

        url = (
            self.base_url
            + "/api/v1/symbol/lookup?"
            + urllib.parse.urlencode(
                {
                    "query": query
                }
            )
        )

        request = urllib.request.Request(
            url,
            headers=self._headers()
        )

        with urllib.request.urlopen(
            request,
            timeout=15
        ) as response:

            payload = json.loads(
                response.read()
            )

        results = []

        for item in payload.get(
            "items",
            []
        )[:limit]:

            results.append(
                {
                    "symbol":
                        item.get(
                            "symbol"
                        ),

                    "name":
                        item.get(
                            "name"
                        ),

                    "currency":
                        item.get(
                            "currency"
                        ),

                    "asset_class":
                        item.get(
                            "assetClass"
                        ),

                    "asset_sub_class":
                        item.get(
                            "assetSubClass"
                        ),

                    "data_source":
                        item.get(
                            "dataSource"
                        ),

                    "exchange":
                        None,

                    "exchangeFullName":
                        None
                }
            )

        return results

    def refresh_profile(
        self,
        symbol,
        data_source="YAHOO"
    ):
        url = (
            self.base_url
            + "/api/v1/admin/gather/profile-data/"
            + data_source
            + "/"
            + urllib.parse.quote(symbol)
        )

        print("REFRESH:", url)

        request = urllib.request.Request(
            url,
            headers=self._headers(),
            method="POST"
        )

        with urllib.request.urlopen(
            request,
            timeout=30
        ) as response:

            print(
                "REFRESH STATUS:",
                response.status
            )

    def get_profile(
        self,
        symbol: str,
        data_source: str = "YAHOO",
    ):
        url = (
            self.base_url.rstrip("/")
            + "/api/v1/asset-profiles/"
            + urllib.parse.quote(
                data_source,
                safe="",
            )
            + "/"
            + urllib.parse.quote(
                symbol,
                safe="",
            )
        )

        request = urllib.request.Request(
            url,
            headers=self._headers(),
            method="GET",
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=15,
            ) as response:
                raw_body = response.read()

                body = raw_body.decode(
                    response.headers.get_content_charset()
                    or "utf-8",
                    errors="replace",
                )

                print(
                    "GHOSTFOLIO RAW RESPONSE",
                    {
                        "symbol": symbol,
                        "status": response.status,
                        "content_type": response.headers.get(
                            "Content-Type"
                        ),
                        "body": body[:10000],
                    },
                    flush=True,
                )

                payload = json.loads(body)

        except HTTPError as error:
            error_body = error.read().decode(
                "utf-8",
                errors="replace",
            )

            print(
                "GHOSTFOLIO HTTP ERROR",
                {
                    "symbol": symbol,
                    "status": error.code,
                    "body": error_body[:5000],
                },
                flush=True,
            )

            if error.code == 404:
                raise GhostfolioProfileNotFoundError(
                    f"Profile not found for "
                    f"{symbol} ({data_source})"
                ) from error

            raise

        except json.JSONDecodeError as error:
            print(
                "GHOSTFOLIO JSON ERROR",
                {
                    "symbol": symbol,
                    "error": str(error),
                    "body": body[:5000],
                },
                flush=True,
            )

            raise

        if not isinstance(payload, dict):
            raise ValueError(
                "Invalid Ghostfolio payload: "
                f"expected dict, received "
                f"{type(payload).__name__}"
            )

        profile = payload.get("assetProfile")

        if not isinstance(profile, dict) or not profile:
            print(
                "GHOSTFOLIO ASSET PROFILE MISSING",
                {
                    "symbol": symbol,
                    "payload_keys": list(payload.keys()),
                },
                flush=True,
            )

            return None

        market_data = payload.get("marketData") or []

        last_price = self._extract_last_price(
            market_data
        )

        country_code = self._extract_country_code(
            profile.get("countries")
        )

        sector = self._extract_sector(
            profile.get("sectors")
        )

        result = {
            "symbol": (
                profile.get("symbol")
                or symbol
            ),
            "name": profile.get("name"),
            "currency": profile.get("currency"),
            "country_code": country_code,
            "sector": sector,
            "website": self._clean_url(
                profile.get("url")
            ),
            "asset_class": profile.get(
                "assetClass"
            ),
            "asset_sub_class": profile.get(
                "assetSubClass"
            ),
            "data_source": (
                profile.get("dataSource")
                or data_source
            ),
            "exchange": None,
            "exchangeFullName": None,
            "last_price": last_price,
            "figi": profile.get("figi"),
            "figi_composite": profile.get(
                "figiComposite"
            ),
            "figi_share_class": profile.get(
                "figiShareClass"
            ),
        }

        print(
            "GHOSTFOLIO PARSED PROFILE",
            json.dumps(
                result,
                default=str,
            ),
            flush=True,
        )

        return result

    def refresh_and_get_profile(
        self,
        symbol: str,
        data_source: str = "YAHOO",
        timeout_seconds: int = 20,
    ):
        self.refresh_profile(
            symbol=symbol,
            data_source=data_source,
        )

        time.sleep(1)

        deadline = (
            time.monotonic()
            + timeout_seconds
        )

        last_profile = None

        while time.monotonic() < deadline:

            try:

                last_profile = self.get_profile(
                    symbol=symbol,
                    data_source=data_source,
                )

                if (
                    last_profile
                    and
                    last_profile.get("symbol")
                    and
                    last_profile.get("name")
                ):
                    return last_profile

            except GhostfolioProfileNotFoundError as error:

                print(
                    "GHOSTFOLIO PROFILE NOT FOUND",
                    {
                        "symbol": symbol,
                        "data_source": data_source,
                        "error": str(error),
                    },
                    flush=True,
                )

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
                "last_profile": last_profile,
            },
            flush=True,
        )

        return last_profile