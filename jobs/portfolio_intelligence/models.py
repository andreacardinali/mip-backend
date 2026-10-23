from dataclasses import asdict, dataclass, field
from typing import Any

from job_runtime.models import JobStatus


@dataclass(frozen=True, slots=True)
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
    related_symbols: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class BranchResult:
    branch: str
    status: JobStatus = JobStatus.SUCCESS
    candidate_count: int = 0
    accepted_count: int = 0
    notification_count: int = 0
    errors: list[str] = field(default_factory=list)
    diagnostics: dict[str, Any] = field(default_factory=dict)

    def add_error(self, message: str) -> None:
        self.errors.append(message)
        if self.status == JobStatus.SUCCESS:
            self.status = JobStatus.PARTIAL

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        return payload
