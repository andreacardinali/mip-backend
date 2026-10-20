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
    if len(rows) != len(batch):
        raise ValueError(f"{label} LLM result count mismatch")

    for source, row in zip(batch, rows, strict=True):
        if not isinstance(row, Mapping):
            raise ValueError(f"{label} LLM result must be an object")
        if row.get("symbol") != source.symbol:
            raise ValueError(f"{label} LLM symbol mismatch")
        yield source, row
