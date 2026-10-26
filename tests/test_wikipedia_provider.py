import httpx

from market_sources.universe.providers.wikipedia import (
    UNIVERSE_DEFINITIONS,
    WikipediaUniverseProvider,
    parse_universe_members,
)

SP500_HTML = """
<table>
<tr><th>Symbol</th><th>Security</th><th>GICS Sector</th></tr>
<tr><td>BRK.B</td><td>Berkshire Hathaway</td><td>Financials</td></tr>
<tr><td>AAPL</td><td>Apple</td><td>Information Technology</td></tr>
</table>
"""


class FakeClient:
    def __init__(self):
        self.headers = None

    def request(self, method, url, **kwargs):
        self.headers = kwargs["headers"]
        return httpx.Response(200, text=SP500_HTML, request=httpx.Request(method, url))


def test_parse_normalizes_symbols() -> None:
    result = parse_universe_members(SP500_HTML, UNIVERSE_DEFINITIONS["SP500"])
    assert [item.symbol for item in result] == ["BRK-B", "AAPL"]
    assert result[0].source == "WIKIPEDIA"


def test_provider_sends_descriptive_user_agent() -> None:
    client = FakeClient()
    provider = WikipediaUniverseProvider(
        user_agent="MIP/1.0 (ops@example.com)",
        retry_policy=__import__(
            "http_client.retry", fromlist=["RetryPolicy"]
        ).RetryPolicy(max_retries=2, jitter_seconds=0),
        client=client,
    )
    result = provider.get_universe_members("SP500")
    assert len(result) == 2
    assert client.headers["User-Agent"] == "MIP/1.0 (ops@example.com)"
