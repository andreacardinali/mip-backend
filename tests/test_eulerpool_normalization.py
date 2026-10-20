from market_data.capabilities import InstrumentType
from market_data.models import InstrumentReference
from market_data.providers.eulerpool import EulerpoolProvider


class Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)

    def request(self, *args, **kwargs):
        return Response(next(self.responses))

    def close(self):
        pass


def test_etf_price_history_is_close_only():
    provider = EulerpoolProvider(
        "key",
        client=Client(
            [
                [
                    {
                        "ticker": "WSPX.MI",
                    }
                ],
                [
                    [1646179200000, 25.19],
                    [1646265600000, 25.20],
                ],
            ]
        ),
    )
    request = type(
        "R",
        (),
        {
            "instrument": InstrumentReference(
                symbol="WSPX.MI",
                isin="XS2427355958",
                instrument_type=InstrumentType.ETF,
            )
        },
    )()
    rows = provider.get_price_history(request)
    assert rows[0].timestamp == 1646179200
    assert rows[0].price == 25.19
