from collections.abc import (
    Iterator,
    Mapping,
    Sequence,
)
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

    if len(rows) != len(batch):
        raise ValueError(
            f"{label} LLM result count mismatch: "
            f"expected {len(batch)}, "
            f"received {len(rows)}"
        )

    for index, (source, row) in enumerate(
        zip(
            batch,
            rows,
            strict=True,
        )
    ):
        if not isinstance(row, Mapping):
            raise ValueError(
                f"{label} LLM result at index " f"{index} must be an object"
            )

        symbol = row.get("symbol")

        if not isinstance(symbol, str) or not symbol:
            raise ValueError(f"{label} LLM result at index " f"{index} missing symbol")

        if symbol != source.symbol:
            raise ValueError(
                f"{label} LLM symbol mismatch "
                f"at index {index}: "
                f"expected {source.symbol!r}, "
                f"received {symbol!r}"
            )

        yield source, row
