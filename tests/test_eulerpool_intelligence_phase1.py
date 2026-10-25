from datetime import UTC, date

from market_data.capabilities import MarketDataCapability
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
        self.responses, self.requests = iter(responses), []

    def request(self, method, url, **kwargs):
        self.requests.append((method, url, kwargs))
        return Response(next(self.responses))

    def close(self):
        pass


def provider(payloads):
    client = Client(payloads)
    return EulerpoolProvider("key", client=client), client


def test_provider_declares_phase1_capabilities():
    supported = EulerpoolProvider.capabilities.supported
    assert {
        MarketDataCapability.NEWS,
        MarketDataCapability.ETF_HOLDINGS,
        MarketDataCapability.EARNINGS_CALENDAR,
        MarketDataCapability.ECONOMIC_CALENDAR,
    } <= supported


def test_normalizes_company_news():
    subject, _ = provider(
        [
            [
                {
                    "id": 7,
                    "related": "AAPL,MSFT",
                    "category": "company",
                    "headline": "Example",
                    "source": "Yahoo",
                    "summary": "Summary",
                    "realUrl": "https://example.test/story",
                    "datetime": 1792456860,
                }
            ]
        ]
    )
    rows = subject.get_news(InstrumentReference(symbol="AAPL"), limit=5)
    assert rows[0].headline == "Example"
    assert rows[0].related_symbols == ("AAPL", "MSFT")
    assert rows[0].published_at.tzinfo == UTC


def test_normalizes_etf_holdings_and_prefers_isin():
    subject, client = provider(
        [
            [
                {
                    "etf_symbol": "VWRP.L",
                    "assetType": "STOCK",
                    "cusip": "67066G104",
                    "isin": "US67066G1040",
                    "name": "NVIDIA",
                    "percent": 5.02826134,
                    "share": 19188359,
                    "symbol": "NVDA",
                    "value": 3294829032.93,
                    "updated_at": "2026-10-20T05:39:57.065Z",
                }
            ]
        ]
    )
    rows = subject.get_etf_holdings(
        InstrumentReference(symbol="VWCE.DE", isin="IE00BK5BQT80")
    )
    assert rows[0].symbol == "NVDA" and rows[0].weight_percent == 5.02826134
    assert client.requests[0][1].endswith("/etf/holdings/IE00BK5BQT80")


def test_normalizes_country_exposure():
    subject, _ = provider(
        [[{"symbol": "VWRP.L", "country": "United States", "exposure": 62.24}]]
    )
    rows = subject.get_etf_countries(
        InstrumentReference(symbol="VWCE.DE", isin="IE00BK5BQT80")
    )
    assert rows[0].name == "United States" and rows[0].exposure == 62.24


def test_normalizes_earnings_calendar():
    subject, _ = provider(
        [
            [
                {
                    "symbol": "TSLA",
                    "date": "2026-10-20",
                    "year": 2026,
                    "quarter": 3,
                    "hour": "amc",
                    "epsEstimate": 0.4343,
                    "revenueEstimate": 28069555287,
                    "isin": "US88160R1014",
                    "name": "Tesla",
                }
            ]
        ]
    )
    rows = subject.get_earnings_calendar(date(2026, 10, 20))
    assert rows[0].symbol == "TSLA" and rows[0].eps_estimate == 0.4343


def test_normalizes_economic_calendar():
    subject, _ = provider(
        [
            [
                {
                    "release_id": 200,
                    "release_name": "CBOE Market Statistics",
                    "release_date": "2026-10-20",
                    "notes": None,
                }
            ]
        ]
    )
    rows = subject.get_economic_calendar(limit=5)
    assert rows[0].external_id == "200" and rows[0].release_date == date(2026, 10, 20)
