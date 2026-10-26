from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from html.parser import HTMLParser

import httpx

from http_client.retry import RetryPolicy, request_with_retry
from market_sources.capabilities import (
    MarketSourceCapability,
    SourceProviderCapabilities,
)
from market_sources.exceptions import (
    InvalidSourceResponseError,
    SourceRateLimitError,
    SourceTemporaryError,
)
from market_sources.models import UniverseMember, UniverseSnapshot


@dataclass(frozen=True, slots=True)
class WikipediaUniverseDefinition:
    code: str
    url: str
    symbol_column: str
    name_columns: tuple[str, ...]
    exchange_columns: tuple[str, ...] = ()
    sector_columns: tuple[str, ...] = ()
    industry_columns: tuple[str, ...] = ()


UNIVERSE_DEFINITIONS = {
    "SP500": WikipediaUniverseDefinition(
        "SP500",
        "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "Symbol",
        ("Security",),
        ("Exchange",),
        ("GICS Sector",),
        ("GICS Sub-Industry",),
    ),
    "NASDAQ100": WikipediaUniverseDefinition(
        "NASDAQ100",
        "https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
        "Ticker",
        ("Company",),
        (),
        ("ICB Industry",),
        ("ICB Subsector",),
    ),
}


class _WikiTableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables = []
        self._table = self._row = self._cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in {"th", "td"} and self._row is not None:
            self._cell = []

    def handle_data(self, data):
        if self._cell is not None:
            self._cell.append(data)

    def handle_endtag(self, tag):
        if tag in {"th", "td"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if self._row:
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None


class WikipediaUniverseProvider:
    name = "wikipedia"
    capabilities = SourceProviderCapabilities(
        frozenset({MarketSourceCapability.UNIVERSE_MEMBERS})
    )

    def __init__(self, *, user_agent, timeout=30.0, retry_policy=None, client=None):
        clean = str(user_agent or "").strip()
        if not clean:
            raise ValueError("WIKIPEDIA_USER_AGENT cannot be empty")
        self.user_agent, self.timeout, self.retry_policy, self._client = (
            clean,
            float(timeout),
            retry_policy or RetryPolicy(),
            client,
        )

    def get_universe_members(self, universe_code):
        code = normalize_universe_code(universe_code)
        try:
            definition = UNIVERSE_DEFINITIONS[code]
        except KeyError as error:
            raise InvalidSourceResponseError(
                f"Wikipedia universe {universe_code!r} is not configured; supported: {', '.join(sorted(UNIVERSE_DEFINITIONS))}"
            ) from error
        client = self._client or httpx.Client(timeout=self.timeout)
        close = self._client is None
        try:
            response = request_with_retry(
                client,
                "GET",
                definition.url,
                policy=self.retry_policy,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml",
                    "Accept-Language": "en-US,en;q=0.8",
                },
            )
            if response.status_code == 429:
                raise SourceRateLimitError(
                    f"Wikipedia rate limited universe {code}; retry_after={response.headers.get('Retry-After')}"
                )
            if response.status_code >= 500:
                raise SourceTemporaryError(
                    f"Wikipedia temporary failure universe {code}: HTTP {response.status_code}"
                )
            response.raise_for_status()
            return parse_universe_members(response.text, definition)
        except httpx.HTTPStatusError as error:
            raise SourceTemporaryError(
                f"Wikipedia request failed universe {code}: HTTP {error.response.status_code}"
            ) from error
        except httpx.RequestError as error:
            raise SourceTemporaryError(
                f"Wikipedia request failed universe {code}: {error}"
            ) from error
        finally:
            if close:
                client.close()

    def get_universe_snapshot(self, universe_code):
        code = normalize_universe_code(universe_code)
        definition = UNIVERSE_DEFINITIONS.get(code)
        members = self.get_universe_members(code)
        return UniverseSnapshot(
            code,
            self.name,
            datetime.now(UTC),
            tuple(members),
            {"source_reference": definition.url if definition else None},
        )


def parse_universe_members(html, definition):
    parser = _WikiTableParser()
    parser.feed(html)
    table = _select_table(parser.tables, definition.symbol_column)
    header = table[0]
    idx = header.index(definition.symbol_column)
    result = []
    seen = set()
    for row in table[1:]:
        if idx >= len(row):
            continue
        symbol = normalize_universe_symbol(row[idx])
        if not symbol or symbol in seen:
            continue
        payload = {
            header[i]: v for i, v in enumerate(row) if i < len(header) and header[i]
        }
        seen.add(symbol)
        result.append(
            UniverseMember(
                definition.code,
                symbol,
                _first(payload, definition.name_columns),
                _first(payload, definition.exchange_columns),
                _first(payload, definition.sector_columns),
                _first(payload, definition.industry_columns),
                definition.url,
                "WIKIPEDIA",
                payload,
            )
        )
    if not result:
        raise InvalidSourceResponseError(
            f"Wikipedia returned no members for {definition.code}"
        )
    return result


def normalize_universe_symbol(value):
    return str(value or "").strip().upper().replace(".", "-")


def normalize_universe_code(value):
    return str(value or "").strip().upper().replace("-", "").replace("_", "")


def _select_table(tables, column):
    for table in tables:
        if table and column in table[0]:
            return table
    raise InvalidSourceResponseError(
        f"Wikipedia table with column {column!r} not found"
    )


def _first(payload, columns):
    for column in columns:
        value = str(payload.get(column) or "").strip()
        if value:
            return value
    return None
