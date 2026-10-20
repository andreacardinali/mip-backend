from collections.abc import Callable, Hashable, Iterator, Sequence


def batched[T](items: Sequence[T], size: int) -> Iterator[Sequence[T]]:
    if size < 1:
        raise ValueError("Batch size must be >= 1")
    for index in range(0, len(items), size):
        yield items[index : index + size]


def deduplicate_by[T, K: Hashable](
    items: Sequence[T],
    key: Callable[[T], K],
) -> list[T]:
    """Keep the last item for each key, preserving last-occurrence order."""
    positions: dict[K, tuple[int, T]] = {}
    for index, item in enumerate(items):
        positions[key(item)] = (index, item)
    return [item for _, item in sorted(positions.values(), key=lambda pair: pair[0])]
