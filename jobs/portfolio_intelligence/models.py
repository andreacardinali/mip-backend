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
    collection_type: str | None = None
    collection_id: str | None = None
    collection_name: str | None = None
    exposure_available: bool = False
    enabled_categories: list[str] = field(default_factory=list)

    def notification_context(self) -> dict[str, Any]:
        return {
            "collectionType": self.collection_type,
            "collectionId": self.collection_id,
            "collectionName": self.collection_name,
            "allocationPct": self.allocation_pct if self.exposure_available else None,
            "positionMarketValue": (
                self.market_value if self.exposure_available else None
            ),
            "exposureAvailable": self.exposure_available,
        }

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def research_context(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "instrument_id": self.instrument_id,
            "symbol": self.symbol,
            "name": self.name,
            "exchange": self.exchange,
            "currency": self.currency,
            "instrument_type": self.instrument_type,
            "related_symbols": self.related_symbols,
        }


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
