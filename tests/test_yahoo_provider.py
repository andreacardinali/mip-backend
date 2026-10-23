import httpx

from market_data.providers.yahoo import YahooProvider


def test_yahoo_live_price_parser_keeps_exact_listing_and_currency():
    request = httpx.Request("GET", "https://example.test/1GOOGL.MI")
    response = httpx.Response(
        200,
        request=request,
        json={
            "chart": {
                "error": None,
                "result": [{
                    "meta": {
                        "symbol": "1GOOGL.MI",
                        "currency": "EUR",
                        "regularMarketPrice": 303.75,
                        "regularMarketTime": 1790955302,
                    },
                    "timestamp": [],
                    "indicators": {"quote": [{"close": []}]},
                }],
            }
        },
    )
    quote = YahooProvider._parse_response(response, "1GOOGL.MI")
    assert quote.price == 303.75
    assert quote.currency == "EUR"
    assert quote.timestamp == 1790955302
    assert quote.source == "yahoo"
