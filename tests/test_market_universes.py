from __future__ import annotations

import pytest

from market_analysis.universes import UniverseDefinition, validate_constituents
from market_data.models import IndexConstituent
from market_data.providers.wikipedia import (
    INDEX_DEFINITIONS,
    _select_table,
    _WikiTableParser,
    normalize_index_symbol,
    parse_index_constituents,
)


def definition(*, minimum: int = 2, maximum: int = 3) -> UniverseDefinition:
    return UniverseDefinition(
        code="TEST",
        name="Test Index",
        universe_type="INDEX",
        minimum_members=minimum,
        maximum_members=maximum,
    )


def test_parser_finds_wikitable_by_symbol_header() -> None:
    parser = _WikiTableParser()
    parser.feed(
        """
        <table class="wikitable sortable">
          <tr><th>Symbol</th><th>Security</th></tr>
          <tr><td>BRK.B</td><td>Berkshire</td></tr>
          <tr><td>AAPL</td><td>Apple</td></tr>
        </table>
        """
    )
    table = _select_table(parser.tables, "Symbol")
    assert table[1][0] == "BRK.B"
    assert normalize_index_symbol(table[1][0]) == "BRK-B"


def test_wikipedia_parser_returns_normalized_market_data_models() -> None:
    result = parse_index_constituents(
        """
        <table>
          <tr><th>Symbol</th><th>Security</th></tr>
          <tr><td>BRK.B</td><td>Berkshire</td></tr>
          <tr><td>AAPL</td><td>Apple</td></tr>
        </table>
        """,
        INDEX_DEFINITIONS["SP500"],
    )
    assert [item.symbol for item in result] == ["BRK-B", "AAPL"]
    assert all(item.index_code == "SP500" for item in result)


def test_validation_rejects_duplicates() -> None:
    items = [
        IndexConstituent(index_code="TEST", symbol="AAPL"),
        IndexConstituent(index_code="TEST", symbol="AAPL"),
    ]
    with pytest.raises(ValueError, match="duplicate"):
        validate_constituents(items, definition())


def test_validation_rejects_suspicious_cardinality() -> None:
    items = [IndexConstituent(index_code="TEST", symbol="AAPL")]
    with pytest.raises(ValueError, match="unexpected member count"):
        validate_constituents(items, definition())
