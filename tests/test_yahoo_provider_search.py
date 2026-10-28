import unittest

from http_client import testing as httpx
from http_client.retry import RetryPolicy
from market_data.capabilities import InstrumentType, MarketDataCapability
from market_data.exceptions import InvalidProviderResponseError
from market_data.providers.yahoo import YahooProvider


class YahooProviderSearchTest(unittest.TestCase):
    def test_declares_symbol_search_capability(self):
        provider = YahooProvider()
        self.assertTrue(
            provider.supports(
                MarketDataCapability.SYMBOL_SEARCH, InstrumentType.UNKNOWN
            )
        )

    def test_search_normalizes_import_label_and_maps_results(self):
        observed = {}

        def handler(request: httpx.Request) -> httpx.Response:
            observed["q"] = request.url.params.get("q")
            return httpx.Response(
                200,
                request=request,
                json={
                    "quotes": [
                        {
                            "symbol": "1GOOGL.MI",
                            "longname": "Alphabet Inc",
                            "currency": "EUR",
                            "exchange": "MIL",
                            "exchDisp": "Milan",
                        },
                        {"symbol": "1GOOGL.MI", "shortname": "duplicate"},
                    ]
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            provider = YahooProvider(
                client=client, retry_policy=RetryPolicy(max_retries=0)
            )
            results = provider.search("1GOOGL.MI - Alphabet Inc", limit=20)
        self.assertEqual(observed["q"], "1GOOGL.MI")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].symbol, "1GOOGL.MI")
        self.assertEqual(results[0].name, "Alphabet Inc")
        self.assertEqual(results[0].currency, "EUR")
        self.assertEqual(results[0].exchange_code, "MIL")
        self.assertEqual(results[0].exchange_name, "Milan")
        self.assertEqual(results[0].source, "yahoo")

    def test_search_returns_empty_list_for_empty_yahoo_result(self):
        def handler(request):
            return httpx.Response(200, request=request, json={"quotes": []})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            self.assertEqual(YahooProvider(client=client).search("NOT-A-SYMBOL"), [])

    def test_search_rejects_malformed_payload(self):
        def handler(request):
            return httpx.Response(200, request=request, json={"quotes": {}})

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(InvalidProviderResponseError):
                YahooProvider(client=client).search("1GOOGL.MI")

    def test_search_honors_limit_and_skips_invalid_rows(self):
        def handler(request):
            return httpx.Response(
                200,
                request=request,
                json={
                    "quotes": [
                        None,
                        {},
                        {"symbol": "AAA", "shortname": "A"},
                        {"symbol": "BBB", "shortname": "B"},
                    ]
                },
            )

        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            results = YahooProvider(client=client).search("A", limit=1)
        self.assertEqual([result.symbol for result in results], ["AAA"])


if __name__ == "__main__":
    unittest.main()
