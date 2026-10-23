import httpx

from market_data.capabilities import InstrumentType, MarketDataCapability
from market_data.providers.yahoo import YahooProvider


def _response(payload):
    request = httpx.Request("GET", "https://example.test/1GOOGL.MI")
    return httpx.Response(200, request=request, json=payload)


def test_yahoo_live_price_parser_keeps_exact_listing_and_currency():
    response = _response(
        {
            "chart": {
                "error": None,
                "result": [
                    {
                        "meta": {
                            "symbol": "1GOOGL.MI",
                            "currency": "EUR",
                            "regularMarketPrice": 303.75,
                            "regularMarketTime": 1790955302,
                        },
                        "timestamp": [],
                        "indicators": {"quote": [{"close": []}]},
                    }
                ],
            }
        }
    )
    quote = YahooProvider._parse_response(response, "1GOOGL.MI")
    assert quote.price == 303.75
    assert quote.currency == "EUR"
    assert quote.timestamp == 1790955302
    assert quote.source == "yahoo"


def test_yahoo_ohlcv_parser_restores_history_support():
    response = _response(
        {
            "chart": {
                "error": None,
                "result": [
                    {
                        "meta": {"currency": "EUR"},
                        "timestamp": [100, 200],
                        "indicators": {
                            "quote": [
                                {
                                    "open": [10, 11],
                                    "high": [12, 13],
                                    "low": [9, 10],
                                    "close": [11, 12],
                                    "volume": [1000, 2000],
                                }
                            ]
                        },
                    }
                ],
            }
        }
    )
    bars = YahooProvider._parse_ohlcv_response(response, "1GOOGL.MI")
    assert [bar.timestamp for bar in bars] == [100, 200]
    assert bars[-1].close == 12
    assert bars[-1].volume == 2000


def test_yahoo_declares_all_routed_capabilities():
    capabilities = YahooProvider.capabilities
    for capability in (
        MarketDataCapability.LIVE_PRICE,
        MarketDataCapability.PRICE_HISTORY,
        MarketDataCapability.OHLCV,
    ):
        assert capabilities.supports(
            capability,
            instrument_type=InstrumentType.UNKNOWN,
            interval="1d" if capability != MarketDataCapability.LIVE_PRICE else None,
        )
