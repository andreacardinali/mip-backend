from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PriceBar:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
