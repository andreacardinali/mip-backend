from dataclasses import asdict, dataclass, field
from typing import Any

@dataclass
class EntityCandidate:
    user_id: str
    entity_id: str
    instrument_id: str
    symbol: str
    name: str
    exchange: str | None
    currency: str | None
    instrument_type: str | None
    quantity: float = 0.0
    market_value: float = 0.0
    allocation_pct: float = 0.0
    in_portfolio: bool = False
    in_watchlist: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass
class BranchResult:
    branch: str
    status: str = "SUCCESS"
    candidate_count: int = 0
    accepted_count: int = 0
    notification_count: int = 0
    errors: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
