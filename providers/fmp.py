import json
import os
import urllib.parse
import urllib.request

from providers.base import (
    MarketDataProvider
)


class FMPProvider(
    MarketDataProvider
):

    def __init__(self):

        self.api_key = os.getenv(
            "FMP_API_KEY"
        )

    def search_symbols(
        self,
        query: str,
        limit: int = 20
    ):

        params = urllib.parse.urlencode(
            {
                "query": query,
                "limit": limit,
                "apikey": self.api_key,
            }
        )

        url = (
            "https://financialmodelingprep.com"
            "/stable/search-symbol?"
            + params
        )

        with urllib.request.urlopen(
            url,
            timeout=10
        ) as response:

            payload = json.loads(
                response.read()
            )

        results = []

        for item in payload:

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

                    "exchange":
                        item.get(
                            "exchange"
                        ),

                    "exchangeFullName":
                        item.get(
                            "exchangeFullName"
                        ),

                    "currency":
                        item.get(
                            "currency"
                        ),

                    "data_source":
                        "FINANCIAL_MODELING_PREP",
                }
            )

        return results

    def get_profile(
        self,
        symbol: str,
        **kwargs
    ):

        params = urllib.parse.urlencode(
            {
                "symbol": symbol,
                "apikey": self.api_key,
            }
        )

        url = (
            "https://financialmodelingprep.com"
            "/stable/profile?"
            + params
        )

        with urllib.request.urlopen(
            url,
            timeout=10
        ) as response:

            payload = json.loads(
                response.read()
            )

        if not payload:

            return None

        p = payload[0]

        return {
            "symbol":
                p.get(
                    "symbol"
                ),

            "name":
                p.get(
                    "companyName"
                ),

            "currency":
                p.get(
                    "currency"
                ),

            "country":
                p.get(
                    "country"
                ),

            "sector":
                p.get(
                    "sector"
                ),

            "website":
                p.get(
                    "website"
                ),

            "exchange":
                p.get(
                    "exchange"
                ),

            "exchangeFullName":
                p.get(
                    "exchangeFullName"
                ),

            "data_source":
                "FINANCIAL_MODELING_PREP",

            "_raw":
                p,
        }


    def find_symbol_metadata(
        self,
        symbol: str
    ):

        rows = self.search_symbols(
            symbol,
            20
        )

        symbol = symbol.upper()

        for row in rows:

            if (
                row.get(
                    "symbol",
                    ""
                ).upper()
                ==
                symbol
            ):

                return row

        return None
