import math
import re
from datetime import UTC, datetime

from http_client import (
    HttpRequestError,
    HttpStatusError,
    RetryPolicy,
    create_http_client,
    request_with_retry,
)
from market_sources.capabilities import (
    MarketSourceCapability,
    SourceProviderCapabilities,
)
from market_sources.exceptions import (
    InvalidSourceResponseError,
    SourceCapabilityUnavailableError,
    SourceRateLimitError,
    SourceTemporaryError,
)
from market_sources.html_tables import HTMLTableParser
from market_sources.models import (
    DiscoveryCandidate,
    DiscoveryRequest,
    DiscoverySnapshot,
    DiscoverySourceType,
)

YAHOO_SCREENERS = {
    DiscoverySourceType.GAINERS: "day_gainers",
    DiscoverySourceType.LOSERS: "day_losers",
    DiscoverySourceType.MOST_ACTIVE: "day_most_active",
}
HTML_MOVERS_URLS = {
    "yahoo_movers_html": {
        DiscoverySourceType.GAINERS: "https://finance.yahoo.com/markets/stocks/gainers/",
        DiscoverySourceType.LOSERS: "https://finance.yahoo.com/markets/stocks/losers/",
        DiscoverySourceType.MOST_ACTIVE: "https://finance.yahoo.com/markets/stocks/most-active/",
    },
    "finviz_movers": {
        DiscoverySourceType.GAINERS: "https://finviz.com/screener.ashx?v=111&s=ta_topgainers",
        DiscoverySourceType.LOSERS: "https://finviz.com/screener.ashx?v=111&s=ta_toplosers",
        DiscoverySourceType.MOST_ACTIVE: "https://finviz.com/screener.ashx?v=111&s=ta_mostactive",
    },
    "stockanalysis_movers": {
        DiscoverySourceType.GAINERS: "https://stockanalysis.com/markets/gainers/",
        DiscoverySourceType.LOSERS: "https://stockanalysis.com/markets/losers/",
        DiscoverySourceType.MOST_ACTIVE: "https://stockanalysis.com/markets/active/",
    },
}


def _symbol(value) -> str | None:
    symbol = str(value or "").strip().upper().replace(".", "-")
    if re.fullmatch(r"[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)?", symbol):
        return symbol
    return None


def _number(value) -> float | None:
    if isinstance(value, dict):
        value = value.get("raw")
    if value is None or isinstance(value, bool):
        return None
    text = str(value).strip().replace(",", "").removesuffix("%")
    multiplier = 1
    if text[-1:].upper() in {"K", "M", "B", "T"}:
        multiplier = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}[text[-1].upper()]
        text = text[:-1]
    try:
        result = float(text) * multiplier
    except (ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _candidate(symbol, rank, name, change, volume, source_type):
    score = volume if source_type == DiscoverySourceType.MOST_ACTIVE else change
    return DiscoveryCandidate(
        symbol=symbol,
        rank=rank,
        name=str(name).strip() if name else None,
        score=abs(score) if score is not None else None,
        source_payload={"change_percent": change, "volume": volume},
    )


def parse_yahoo_movers(payload, request: DiscoveryRequest):
    try:
        finance = payload["finance"]
        if finance.get("error"):
            raise ValueError("Yahoo screener returned an error")
        quotes = finance["result"][0]["quotes"]
        if not isinstance(quotes, list):
            raise ValueError("Quotes must be a list")
    except (KeyError, IndexError, TypeError, AttributeError, ValueError) as exc:
        raise InvalidSourceResponseError("Invalid Yahoo movers response") from exc
    candidates = []
    seen = set()
    for rank, quote in enumerate(quotes, start=1):
        if not isinstance(quote, dict):
            continue
        symbol = _symbol(quote.get("symbol"))
        if not symbol or symbol in seen or quote.get("quoteType", "EQUITY") != "EQUITY":
            continue
        seen.add(symbol)
        candidates.append(
            _candidate(
                symbol,
                rank,
                quote.get("longName") or quote.get("shortName"),
                _number(quote.get("regularMarketChangePercent")),
                _number(quote.get("regularMarketVolume")),
                request.source_type,
            )
        )
        if len(candidates) >= request.limit:
            break
    if not candidates:
        raise InvalidSourceResponseError("Yahoo movers returned no valid candidates")
    return tuple(candidates)


def parse_html_movers(html: str, request: DiscoveryRequest):
    parser = HTMLTableParser()
    parser.feed(html)
    candidates = []
    seen = set()
    for table in parser.tables:
        for header_index, row in enumerate(table):
            headers = [cell.strip().lower() for cell in row]
            symbol_index = next(
                (
                    index
                    for index, header in enumerate(headers)
                    if header in {"symbol", "ticker"}
                ),
                None,
            )
            if symbol_index is None:
                continue
            name_index = next(
                (
                    index
                    for index, header in enumerate(headers)
                    if header in {"company", "name", "company name"}
                ),
                None,
            )
            change_index = next(
                (
                    index
                    for index, header in enumerate(headers)
                    if header in {"change %", "% change", "change (%)", "%change"}
                ),
                None,
            )
            if change_index is None and "change" in headers:
                change_index = headers.index("change")
            volume_index = headers.index("volume") if "volume" in headers else None
            for rank, cells in enumerate(table[header_index + 1 :], start=1):
                if len(cells) < len(headers):
                    continue
                symbol = _symbol(cells[symbol_index])
                if not symbol or symbol in seen:
                    continue
                seen.add(symbol)
                candidates.append(
                    _candidate(
                        symbol,
                        rank,
                        cells[name_index] if name_index is not None else None,
                        (
                            _number(cells[change_index])
                            if change_index is not None
                            else None
                        ),
                        (
                            _number(cells[volume_index])
                            if volume_index is not None
                            else None
                        ),
                        request.source_type,
                    )
                )
                if len(candidates) >= request.limit:
                    return tuple(candidates)
            break
    if not candidates:
        raise InvalidSourceResponseError("Movers HTML returned no valid candidates")
    return tuple(candidates)


class _MoversDiscoveryProvider:
    capabilities = SourceProviderCapabilities(
        frozenset({MarketSourceCapability.DISCOVERY})
    )

    def __init__(self, *, user_agent, timeout=30.0, retry_policy=None, client=None):
        if not user_agent or timeout <= 0:
            raise ValueError("Movers require a user agent and positive timeout")
        self.user_agent = user_agent
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client

    def discover(self, request: DiscoveryRequest) -> DiscoverySnapshot:
        url, params = self._endpoint(request)
        client = self._client or create_http_client(timeout=self.timeout)
        try:
            response = request_with_retry(
                client,
                "GET",
                url,
                policy=self.retry_policy,
                params=params,
                headers={"User-Agent": self.user_agent, "Accept": self.accept},
                timeout=self.timeout,
            )
            candidates = self._parse(response, request)
            return DiscoverySnapshot(
                source_type=request.source_type,
                provider_name=self.name,
                observed_at=datetime.now(UTC),
                requested_limit=request.limit,
                candidates=candidates,
                source_reference=str(response.url),
                source_metadata={"returned_count": len(candidates)},
            )
        except HttpStatusError as exc:
            if exc.response.status_code == 429:
                raise SourceRateLimitError(f"{self.name} rate limited") from exc
            raise SourceTemporaryError(f"{self.name} HTTP error") from exc
        except HttpRequestError as exc:
            raise SourceTemporaryError(f"{self.name} request failed") from exc
        except ValueError as exc:
            raise InvalidSourceResponseError(f"Invalid {self.name} response") from exc
        finally:
            if self._client is None:
                client.close()


class YahooMoversDiscoveryProvider(_MoversDiscoveryProvider):
    name = "yahoo_movers"
    accept = "application/json"

    def _endpoint(self, request):
        if request.source_type not in YAHOO_SCREENERS:
            raise SourceCapabilityUnavailableError("Unsupported Yahoo movers category")
        return (
            "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved",
            {"scrIds": YAHOO_SCREENERS[request.source_type], "count": request.limit},
        )

    def _parse(self, response, request):
        return parse_yahoo_movers(response.json(), request)


class HTMLMoversDiscoveryProvider(_MoversDiscoveryProvider):
    accept = "text/html"

    def __init__(self, *, name, **kwargs):
        if name not in HTML_MOVERS_URLS:
            raise ValueError(f"Unknown movers HTML provider: {name}")
        super().__init__(**kwargs)
        self.name = name

    def _endpoint(self, request):
        if request.source_type not in HTML_MOVERS_URLS[self.name]:
            raise SourceCapabilityUnavailableError("Unsupported HTML movers category")
        return HTML_MOVERS_URLS[self.name][request.source_type], None

    def _parse(self, response, request):
        return parse_html_movers(response.text, request)
