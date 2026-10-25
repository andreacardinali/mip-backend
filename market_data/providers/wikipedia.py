from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser

import httpx

from http_client.retry import RetryPolicy, request_with_retry
from market_data.capabilities import (
    InstrumentType,
    MarketDataCapability,
    ProviderCapabilities,
)
from market_data.exceptions import (
    InvalidProviderResponseError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)
from market_data.models import IndexConstituent


@dataclass(frozen=True, slots=True)
class WikipediaIndexDefinition:
    code: str
    url: str
    symbol_column: str
    name_columns: tuple[str, ...]
    exchange_columns: tuple[str, ...] = ()
    sector_columns: tuple[str, ...] = ()
    industry_columns: tuple[str, ...] = ()


INDEX_DEFINITIONS = {
    "SP500": WikipediaIndexDefinition(
        code="SP500",
        url="https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        symbol_column="Symbol",
        name_columns=("Security",),
        exchange_columns=("Exchange",),
        sector_columns=("GICS Sector",),
        industry_columns=("GICS Sub-Industry",),
    ),
    "NASDAQ100": WikipediaIndexDefinition(
        code="NASDAQ100",
        url="https://en.wikipedia.org/wiki/List_of_NASDAQ-100_companies",
        symbol_column="Ticker",
        name_columns=("Company",),
        sector_columns=("ICB Industry",),
        industry_columns=("ICB Subsector",),
    ),
}


class _WikiTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

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


class WikipediaProvider:
    name = "wikipedia"
    capabilities = ProviderCapabilities(
        supported=frozenset({MarketDataCapability.INDEX_CONSTITUENTS}),
        instrument_types={
            MarketDataCapability.INDEX_CONSTITUENTS: frozenset({InstrumentType.INDEX})
        },
    )

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        client=None,
    ) -> None:
        clean = str(user_agent or "").strip()
        if not clean:
            raise ValueError("WIKIPEDIA_USER_AGENT cannot be empty")
        self.user_agent = clean
        self.timeout = float(timeout)
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client

    def get_index_constituents(self, index_code: str) -> list[IndexConstituent]:
        code = _normalize_index_code(index_code)
        try:
            definition = INDEX_DEFINITIONS[code]
        except KeyError as error:
            supported = ", ".join(sorted(INDEX_DEFINITIONS))
            raise InvalidProviderResponseError(
                f"Wikipedia index {index_code!r} is not configured; supported: {supported}"
            ) from error

        client = self._client or httpx.Client(timeout=self.timeout)
        close_client = self._client is None
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
                raise ProviderRateLimitError(
                    f"Wikipedia rate limited index {code}; retry_after={response.headers.get('Retry-After')}"
                )
            if response.status_code >= 500:
                raise ProviderTemporaryError(
                    f"Wikipedia temporary failure index {code}: HTTP {response.status_code}"
                )
            response.raise_for_status()
            return parse_index_constituents(response.text, definition)
        except httpx.HTTPStatusError as error:
            raise ProviderTemporaryError(
                f"Wikipedia request failed index {code}: HTTP {error.response.status_code}"
            ) from error
        except httpx.RequestError as error:
            raise ProviderTemporaryError(
                f"Wikipedia request failed index {code}: {error}"
            ) from error
        finally:
            if close_client:
                client.close()


def parse_index_constituents(
    html: str, definition: WikipediaIndexDefinition
) -> list[IndexConstituent]:
    parser = _WikiTableParser()
    parser.feed(html)
    table = _select_table(parser.tables, definition.symbol_column)
    header = table[0]
    symbol_index = header.index(definition.symbol_column)
    result = []
    seen = set()

    for row in table[1:]:
        if symbol_index >= len(row):
            continue
        symbol = normalize_index_symbol(row[symbol_index])
        if not symbol or symbol in seen:
            continue
        payload = {
            header[index]: value
            for index, value in enumerate(row)
            if index < len(header) and header[index]
        }
        seen.add(symbol)
        result.append(
            IndexConstituent(
                index_code=definition.code,
                symbol=symbol,
                name=_first(payload, definition.name_columns),
                exchange_code=_first(payload, definition.exchange_columns),
                sector=_first(payload, definition.sector_columns),
                industry=_first(payload, definition.industry_columns),
                source_reference=definition.url,
                source="WIKIPEDIA",
                raw_payload=payload,
            )
        )

    if not result:
        raise InvalidProviderResponseError(
            f"Wikipedia returned no constituents for {definition.code}"
        )
    return result


def normalize_index_symbol(value: str) -> str:
    return str(value or "").strip().upper().replace(".", "-")


def _normalize_index_code(value: str) -> str:
    return str(value or "").strip().upper().replace("-", "").replace("_", "")


def _select_table(tables, symbol_column):
    for table in tables:
        if table and symbol_column in table[0]:
            return table
    raise InvalidProviderResponseError(
        f"Wikipedia table with column {symbol_column!r} not found"
    )


def _first(payload: dict[str, str], columns: tuple[str, ...]) -> str | None:
    for column in columns:
        value = str(payload.get(column) or "").strip()
        if value:
            return value
    return None
