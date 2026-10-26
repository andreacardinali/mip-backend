from collections import Counter
from dataclasses import dataclass

from market_sources.models import UniverseMember


@dataclass(frozen=True, slots=True)
class UniverseDefinition:
    code: str
    name: str
    universe_type: str
    minimum_members: int
    maximum_members: int


DEFAULT_UNIVERSES = (
    UniverseDefinition("SP500", "S&P 500", "INDEX", 490, 510),
    UniverseDefinition("NASDAQ100", "NASDAQ-100", "INDEX", 95, 110),
)


def validate_constituents(constituents: list[UniverseMember], definition):
    if not constituents:
        raise ValueError(f"{definition.code}: empty constituent set")
    wrong = sorted(
        {x.universe_code for x in constituents if x.universe_code != definition.code}
    )
    if wrong:
        raise ValueError(
            f"{definition.code}: provider returned members for: {', '.join(wrong)}"
        )
    counts = Counter(x.symbol for x in constituents)
    duplicates = sorted(x for x, c in counts.items() if c > 1)
    if duplicates:
        raise ValueError(
            f"{definition.code}: duplicate symbols: {', '.join(duplicates[:10])}"
        )
    if (
        not definition.minimum_members
        <= len(constituents)
        <= definition.maximum_members
    ):
        raise ValueError(
            f"{definition.code}: unexpected member count {len(constituents)}; expected {definition.minimum_members}-{definition.maximum_members}"
        )
