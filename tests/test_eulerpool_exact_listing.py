from http_client import testing as httpx
from market_data.capabilities import InstrumentType
from market_data.exceptions import InstrumentNotFoundError
from market_data.models import InstrumentReference
from market_data.providers.eulerpool import EulerpoolProvider


def test_rejects_listing_when_search_resolves_another_ticker():
    def handler(request):
        if request.url.path.endswith("/screener/search/VWCE.MI"):
            return httpx.Response(200, request=request, json=[{"ticker": "VWCE.DE"}])
        raise AssertionError(request.url)

    provider = EulerpoolProvider(
        "token",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    request = type(
        "Request",
        (),
        {
            "instrument": InstrumentReference(
                symbol="VWCE.MI",
                isin="IE00BK5BQT80",
                instrument_type=InstrumentType.ETF,
                currency="EUR",
                exchange_code="XMIL",
            )
        },
    )()

    try:
        provider.get_price_history(request)
    except InstrumentNotFoundError:
        pass
    else:
        raise AssertionError("Expected exact listing rejection")
