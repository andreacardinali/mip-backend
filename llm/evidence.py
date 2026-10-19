from collections.abc import Iterable, Mapping, Sequence
from typing import Any


def select_sources(
    source_indices: Iterable[object] | None,
    available_sources: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Select valid, unique LLM sources while preserving requested order.

    Invalid indices, duplicate indices, malformed source entries, and references
    to unavailable sources are ignored. Returned dictionaries are copies, so
    callers cannot mutate the provider response accidentally.
    """
    sources_by_index: dict[int, Mapping[str, Any]] = {}

    for source in available_sources:
        if not isinstance(source, Mapping):
            continue
        source_index = source.get("sourceIndex")
        if not isinstance(source_index, int) or isinstance(source_index, bool):
            continue
        sources_by_index.setdefault(source_index, source)

    selected: list[dict[str, Any]] = []
    selected_indices: set[int] = set()

    for value in source_indices or []:
        if not isinstance(value, int) or isinstance(value, bool):
            continue
        if value in selected_indices:
            continue
        source = sources_by_index.get(value)
        if source is None:
            continue

        selected.append(dict(source))
        selected_indices.add(value)

    return selected


# Temporary compatibility alias for callers migrated incrementally.
validated_sources = select_sources
