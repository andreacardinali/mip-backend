from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Protocol


class SymbolCandidate(Protocol):
    symbol: str


def matched_batch_rows[T: SymbolCandidate](
    batch: Sequence[T],
    response_data: Mapping[str, Any],
    *,
    label: str,
) -> Iterator[tuple[T, Mapping[str, Any]]]:
    rows = response_data.get("results") or []

    if not isinstance(rows, list):
        raise ValueError(f"{label} LLM results must be a list")

    rows_by_symbol: dict[str, Mapping[str, Any]] = {}

    for row in rows:
        if not isinstance(row, Mapping):
            raise ValueError(f"{label} LLM result must be an object")

        symbol = row.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            raise ValueError(f"{label} LLM result missing symbol")

        if symbol in rows_by_symbol:
            raise ValueError(f"{label} LLM duplicate symbol: {symbol}")

        rows_by_symbol[symbol] = row

    expected_symbols = {source.symbol for source in batch}
    unexpected_symbols = set(rows_by_symbol) - expected_symbols

    if unexpected_symbols:
        raise ValueError(
            f"{label} LLM symbol mismatch: "
            f"unexpected symbols {sorted(unexpected_symbols)}"
        )

    for source in batch:
        row = rows_by_symbol.get(source.symbol)

        if row is None:
            row = {
                "symbol": source.symbol,
                "events": [],
            }

        yield source, row
