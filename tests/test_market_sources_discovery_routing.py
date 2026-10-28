from datetime import UTC, datetime

import httpx
import pytest

from market_sources.capabilities import (
    MarketSourceCapability,
    SourceProviderCapabilities,
)
from market_sources.config import MarketSourceConfig
from market_sources.discovery.providers.movers import (
    HTML_MOVERS_URLS,
    YAHOO_SCREENERS,
    HTMLMoversDiscoveryProvider,
    YahooMoversDiscoveryProvider,
    parse_html_movers,
    parse_yahoo_movers,
)
from market_sources.exceptions import (
    InvalidSourceResponseError,
    SourceCapabilityUnavailableError,
    SourceRateLimitError,
    SourceTemporaryError,
)
from market_sources.models import (
    DiscoveryCandidate,
    DiscoveryRequest,
    DiscoverySnapshot,
    DiscoverySourceType,
)
from market_sources.registry import SourceProviderRegistry
from market_sources.service import MarketSourceService


class FakeDiscoveryProvider:
    name = "fake"
    capabilities = SourceProviderCapabilities(
        frozenset({MarketSourceCapability.DISCOVERY})
    )

    def discover(self, request):
        return DiscoverySnapshot(
            source_type=request.source_type,
            provider_name=self.name,
            observed_at=datetime.now(UTC),
            candidates=(DiscoveryCandidate(symbol="AAPL", rank=1),),
        )


def test_discovery_routes_through_market_source_service() -> None:
    config = MarketSourceConfig(
        provider_orders={
            MarketSourceCapability.UNIVERSE_MEMBERS: ("fake",),
            MarketSourceCapability.DISCOVERY: ("fake",),
        },
        http_timeout=1,
        http_max_retries=0,
        http_retry_base_seconds=0,
    )
    service = MarketSourceService(
        SourceProviderRegistry([FakeDiscoveryProvider()]),
        config,
    )
    snapshot = service.discover(
        DiscoveryRequest(
            source_type=DiscoverySourceType.REDDIT_TRENDING,
            limit=10,
        )
    )
    assert snapshot.provider_name == "fake"
    assert snapshot.candidates[0].symbol == "AAPL"


def test_default_factory_routes_social_discovery_without_fetching_movers(monkeypatch):
    from market_sources import factory

    monkeypatch.setattr(factory, "settings", {})
    service = factory.create_market_source_service()
    assert {item["name"] for item in service.registry.describe()} == {
        "wikipedia",
        "apewisdom",
        "yahoo_movers",
        *HTML_MOVERS_URLS,
    }
    assert service.config.provider_orders[MarketSourceCapability.DISCOVERY] == (
        "apewisdom",
        "yahoo_movers",
        *HTML_MOVERS_URLS,
    )
    calls = []

    def respond(request):
        calls.append(request.url.host)
        assert request.url.host == "apewisdom.io"
        return httpx.Response(200, json={"results": [{"ticker": "AAPL"}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        service.registry.get("apewisdom")._client = client
        snapshot = service.discover(
            DiscoveryRequest(DiscoverySourceType.REDDIT_TRENDING, limit=1)
        )
    assert snapshot.provider_name == "apewisdom"
    assert snapshot.candidates[0].symbol == "AAPL"
    assert calls == ["apewisdom.io"]


@pytest.mark.parametrize(
    "source_type",
    [
        DiscoverySourceType.GAINERS,
        DiscoverySourceType.LOSERS,
        DiscoverySourceType.MOST_ACTIVE,
    ],
)
def test_default_factory_routes_movers_discovery(monkeypatch, source_type):
    from market_sources import factory

    monkeypatch.setattr(factory, "settings", {})
    service = factory.create_market_source_service()

    def respond(request):
        assert request.url.host == "query2.finance.yahoo.com"
        assert request.url.params["count"] == "1"
        assert request.url.params["scrIds"] == YAHOO_SCREENERS[source_type]
        return httpx.Response(
            200,
            json={"finance": {"result": [{"quotes": [{"symbol": "AAPL"}]}]}},
        )

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        service.registry.get("apewisdom")._client = client
        service.registry.get("yahoo_movers")._client = client
        snapshot = service.discover(DiscoveryRequest(source_type, limit=1))
    assert snapshot.provider_name == "yahoo_movers"
    assert snapshot.candidates[0].symbol == "AAPL"


def test_factory_honors_explicit_discovery_order_and_provider_settings(monkeypatch):
    from market_sources import factory

    monkeypatch.setattr(
        factory,
        "settings",
        {
            "MARKET_SOURCES_DISCOVERY_ORDER": "apewisdom",
            "APEWISDOM_USER_AGENT": "MIP/custom",
            "APEWISDOM_HTTP_TIMEOUT": "3.5",
        },
    )
    service = factory.create_market_source_service()
    provider = service.registry.get("apewisdom")
    assert provider.timeout == 3.5
    assert service.config.provider_orders[MarketSourceCapability.DISCOVERY] == (
        "apewisdom",
    )

    def respond(request):
        assert request.url.host == "apewisdom.io"
        assert request.headers["User-Agent"] == "MIP/custom"
        return httpx.Response(200, json={"results": [{"ticker": "AAPL"}]})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider._client = client
        snapshot = service.discover(
            DiscoveryRequest(DiscoverySourceType.REDDIT_TRENDING)
        )
    assert snapshot.provider_name == "apewisdom"


@pytest.mark.parametrize("name", ["yahoo_movers", *HTML_MOVERS_URLS])
def test_factory_honors_explicit_movers_order_and_settings(monkeypatch, name):
    from market_sources import factory

    monkeypatch.setattr(
        factory,
        "settings",
        {
            "MARKET_SOURCES_DISCOVERY_ORDER": name,
            f"{name.upper()}_USER_AGENT": "MIP/custom",
            f"{name.upper()}_HTTP_TIMEOUT": "3.5",
        },
    )
    service = factory.create_market_source_service()
    provider = service.registry.get(name)
    assert service.config.provider_orders[MarketSourceCapability.DISCOVERY] == (name,)
    assert provider.timeout == 3.5

    def respond(request):
        assert request.headers["User-Agent"] == "MIP/custom"
        if name == "yahoo_movers":
            assert request.url.host == "query2.finance.yahoo.com"
            return httpx.Response(
                200,
                json={"finance": {"result": [{"quotes": [{"symbol": "AAPL"}]}]}},
            )
        assert str(request.url) == HTML_MOVERS_URLS[name][DiscoverySourceType.GAINERS]
        return httpx.Response(200, text=MOVERS_HTML)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider._client = client
        snapshot = service.discover(DiscoveryRequest(DiscoverySourceType.GAINERS))
    assert snapshot.provider_name == name


def test_rate_limit_allows_fallback_without_unexpected_error(caplog):
    class RateLimitedProvider(FakeDiscoveryProvider):
        name = "limited"

        def discover(self, request):
            raise SourceRateLimitError("rate limited")

    config = MarketSourceConfig.from_settings(
        {"MARKET_SOURCES_DISCOVERY_ORDER": "limited,fake"}
    )
    service = MarketSourceService(
        SourceProviderRegistry([RateLimitedProvider(), FakeDiscoveryProvider()]),
        config,
    )
    snapshot = service.discover(DiscoveryRequest(DiscoverySourceType.REDDIT_TRENDING))
    assert snapshot.provider_name == "fake"
    assert not any(record.exc_info for record in caplog.records)


@pytest.mark.parametrize("source_type", list(YAHOO_SCREENERS))
def test_yahoo_movers_normalizes_candidates_and_scores(source_type):
    payload = {
        "finance": {
            "result": [
                {
                    "quotes": [
                        {"symbol": "BTC-USD", "quoteType": "CRYPTOCURRENCY"},
                        {
                            "symbol": "brk.b",
                            "longName": "Berkshire Hathaway",
                            "regularMarketChangePercent": {"raw": -5.5},
                            "regularMarketVolume": 1_200_000,
                            "regularMarketPrice": 400,
                        },
                        {"symbol": "BRK-B"},
                        {"symbol": "AAPL"},
                    ]
                }
            ]
        }
    }
    candidates = parse_yahoo_movers(payload, DiscoveryRequest(source_type, limit=1))
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate.symbol == "BRK-B"
    assert candidate.rank == 2
    assert candidate.name == "Berkshire Hathaway"
    assert candidate.score == (
        1_200_000 if source_type == DiscoverySourceType.MOST_ACTIVE else 5.5
    )
    assert candidate.source_payload == {"change_percent": -5.5, "volume": 1_200_000}
    all_candidates = parse_yahoo_movers(payload, DiscoveryRequest(source_type))
    assert [candidate.symbol for candidate in all_candidates] == ["BRK-B", "AAPL"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"finance": {"result": []}},
        {"finance": {"result": [{"quotes": []}]}},
        {"finance": {"error": {"description": "denied"}}},
    ],
)
def test_invalid_yahoo_movers_response_allows_fallback(payload):
    with pytest.raises(InvalidSourceResponseError):
        parse_yahoo_movers(payload, DiscoveryRequest(DiscoverySourceType.GAINERS))


def test_yahoo_movers_discards_nonfinite_metrics():
    candidates = parse_yahoo_movers(
        {
            "finance": {
                "result": [
                    {
                        "quotes": [
                            {
                                "symbol": "AAPL",
                                "regularMarketChangePercent": "NaN",
                                "regularMarketVolume": "inf",
                            }
                        ]
                    }
                ]
            }
        },
        DiscoveryRequest(DiscoverySourceType.GAINERS),
    )
    assert candidates[0].score is None
    assert candidates[0].source_payload == {"change_percent": None, "volume": None}


MOVERS_HTML = """
<table><tr><td>Navigation</td></tr><tr><td>
<table><tr><th>Ticker</th><th>Company</th><th>Change</th><th>Change %</th><th>Volume</th></tr>
<tr><td>BRK.B</td><td>Berkshire &amp; Co</td><td>-30</td><td>-5.5%</td><td>1.2M</td></tr>
<tr><td>BRK-B</td><td>Duplicate</td><td>-30</td><td>-5.5%</td><td>1.2M</td></tr>
<tr><td>AAPL</td><td>Apple</td><td>3</td><td>2%</td><td>900K</td></tr>
</table></td></tr></table>
"""


@pytest.mark.parametrize("name", list(HTML_MOVERS_URLS))
@pytest.mark.parametrize("source_type", list(YAHOO_SCREENERS))
def test_html_movers_providers_return_discovery_only(name, source_type):
    def respond(request):
        assert str(request.url) == HTML_MOVERS_URLS[name][source_type]
        assert request.headers["User-Agent"] == "MIP/test"
        return httpx.Response(200, text=MOVERS_HTML)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        provider = HTMLMoversDiscoveryProvider(
            name=name, user_agent="MIP/test", client=client
        )
        snapshot = provider.discover(DiscoveryRequest(source_type, limit=1))
        assert not client.is_closed
    assert snapshot.provider_name == name
    assert snapshot.requested_limit == 1
    assert snapshot.returned_count == 1
    assert snapshot.candidates[0].name == "Berkshire & Co"
    assert snapshot.candidates[0].score == (
        1_200_000 if source_type == DiscoverySourceType.MOST_ACTIVE else 5.5
    )
    assert not hasattr(provider, "get_ohlcv")
    assert not hasattr(provider, "get_price")
    candidates = parse_html_movers(MOVERS_HTML, DiscoveryRequest(source_type))
    assert [candidate.symbol for candidate in candidates] == ["BRK-B", "AAPL"]
    assert [candidate.rank for candidate in candidates] == [1, 3]


@pytest.mark.parametrize("name", ["yahoo_movers", *HTML_MOVERS_URLS])
def test_movers_rejects_social_category_before_http(name):
    def unexpected(request):
        pytest.fail("Movers must reject social discovery before HTTP")

    with httpx.Client(transport=httpx.MockTransport(unexpected)) as client:
        kwargs = {"user_agent": "MIP/test", "client": client}
        provider = (
            YahooMoversDiscoveryProvider(**kwargs)
            if name == "yahoo_movers"
            else HTMLMoversDiscoveryProvider(name=name, **kwargs)
        )
        with pytest.raises(SourceCapabilityUnavailableError):
            provider.discover(DiscoveryRequest(DiscoverySourceType.REDDIT_TRENDING))


@pytest.mark.parametrize("status", [200, 429, 503])
def test_factory_falls_back_from_yahoo_to_html(monkeypatch, status):
    from market_sources import factory

    monkeypatch.setattr(factory, "settings", {"MARKET_SOURCES_HTTP_MAX_RETRIES": "0"})
    service = factory.create_market_source_service()
    calls = []

    def respond(request):
        calls.append(request.url.host)
        if request.url.host == "query2.finance.yahoo.com":
            return httpx.Response(
                status, json={"finance": {"result": [{"quotes": []}]}}
            )
        assert request.url.host == "finance.yahoo.com"
        return httpx.Response(200, text=MOVERS_HTML)

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        for name in ("apewisdom", "yahoo_movers", *HTML_MOVERS_URLS):
            service.registry.get(name)._client = client
        snapshot = service.discover(DiscoveryRequest(DiscoverySourceType.GAINERS))
        assert not client.is_closed
    assert snapshot.provider_name == "yahoo_movers_html"
    assert calls == ["query2.finance.yahoo.com", "finance.yahoo.com"]


@pytest.mark.parametrize(
    "status, error",
    [
        (429, SourceRateLimitError),
        (503, SourceTemporaryError),
        (200, InvalidSourceResponseError),
    ],
)
def test_movers_http_errors_are_centralized(status, error):
    with httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(status, text="not JSON")
        )
    ) as client:
        provider = YahooMoversDiscoveryProvider(user_agent="MIP/test", client=client)
        with pytest.raises(error):
            provider.discover(DiscoveryRequest(DiscoverySourceType.GAINERS))
        assert not client.is_closed
