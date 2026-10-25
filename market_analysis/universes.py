from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from market_data.models import IndexConstituent


@dataclass(frozen=True, slots=True)
class UniverseDefinition:
    code: str
    name: str
    universe_type: str
    minimum_members: int
    maximum_members: int


DEFAULT_UNIVERSES = (
    UniverseDefinition(
        code="SP500",
        name="S&P 500",
        universe_type="INDEX",
        minimum_members=490,
        maximum_members=510,
    ),
    UniverseDefinition(
        code="NASDAQ100",
        name="NASDAQ-100",
        universe_type="INDEX",
        minimum_members=95,
        maximum_members=110,
    ),
)


def validate_constituents(
    constituents: list[IndexConstituent],
    definition: UniverseDefinition,
) -> None:
    if not constituents:
        raise ValueError(f"{definition.code}: empty constituent set")

    wrong_index = sorted(
        {item.index_code for item in constituents if item.index_code != definition.code}
    )
    if wrong_index:
        raise ValueError(
            f"{definition.code}: provider returned constituents for: {', '.join(wrong_index)}"
        )

    counts = Counter(item.symbol for item in constituents)
    duplicates = sorted(symbol for symbol, count in counts.items() if count > 1)
    if duplicates:
        raise ValueError(
            f"{definition.code}: duplicate symbols: {', '.join(duplicates[:10])}"
        )

    count = len(constituents)
    if not definition.minimum_members <= count <= definition.maximum_members:
        raise ValueError(
            f"{definition.code}: unexpected member count {count}; expected "
            f"{definition.minimum_members}-{definition.maximum_members}"
        )
