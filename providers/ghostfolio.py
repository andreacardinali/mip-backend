import json
import os
import re
import time
import urllib.parse
import urllib.request
from urllib.error import HTTPError


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

    def __init__(self):

        self.base_url = os.getenv(
            "GHOSTFOLIO_BASE_URL"
        )

        self.security_token = os.getenv(
            "GHOSTFOLIO_SECURITY_TOKEN"
        )

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
        data_source: str = "YAHOO"
    ):

        url = (
            self.base_url
            + "/api/v1/asset-profiles/"
            + data_source
            + "/"
            + urllib.parse.quote(
                symbol
            )
        )

        request = urllib.request.Request(
            url,
            headers=self._headers()
        )

        try:
            with urllib.request.urlopen(
                request,
                timeout=15
            ) as response:

                payload = json.loads(
                    response.read()
                )

        except HTTPError as error:

            if error.code == 404:
                return None

            raise

        profile = payload.get(
            "assetProfile",
            {}
        )

        last_price = None

        market_data = payload.get(
            "marketData",
            []
        )

        if market_data:

            last_row = market_data[-1]

            last_price = (
                last_row.get(
                    "marketPrice"
                )
            )

        country = None

        if profile.get(
            "countries"
        ):

            country = (
                profile["countries"][0]
                .get("code")
            )

        sector = None

        if profile.get(
            "sectors"
        ):

            sector = (
                profile["sectors"][0]
                .get("name")
            )

        if last_price is None:

            print(
                "NO PRICE",
                symbol,
                json.dumps(payload)[:1000],
                flush=True
            )
            
        if country is None:

            print(
                "NO COUNTRY",
                symbol,
                json.dumps(payload)[:1000],
                flush=True
            )
        return {
            "symbol":
                profile.get(
                    "symbol"
                ),

            "name":
                profile.get(
                    "name"
                ),

            "currency":
                profile.get(
                    "currency"
                ),

            "country_code":
                country,

            "sector":
                sector,

            "website":
                self._clean_url(
                    profile.get(
                        "url"
                    )
                ),

            "asset_class":
                profile.get(
                    "assetClass"
                ),

            "asset_sub_class":
                profile.get(
                    "assetSubClass"
                ),

            "data_source":
                profile.get(
                    "dataSource"
                ),

            "exchange":
                None,

            "exchangeFullName":
                None,

            "last_price":
                last_price,

            "figi":
                profile.get(
                    "figi"
                ),

            "figi_composite":
                profile.get(
                    "figiComposite"
                ),

            "figi_share_class":
                profile.get(
                    "figiShareClass"
                )
        }

    def refresh_and_get_profile(
        self,
        symbol: str,
        data_source: str = "YAHOO",
        timeout_seconds: int = 20
    ):

        self.refresh_profile(
            symbol=symbol,
            data_source=data_source
        )

        # lascia partire il worker
        time.sleep(1)

        deadline = (
            time.time()
            + timeout_seconds
        )

        while time.time() < deadline:

            try:

                profile = self.get_profile(
                    symbol=symbol,
                    data_source=data_source
                )

                if (
                    profile
                    and profile.get("name")
                    and profile.get("asset_class")
                    and profile.get("last_price") is not None
                ):
                    return profile

            except Exception:
                pass

            time.sleep(1)

        return None