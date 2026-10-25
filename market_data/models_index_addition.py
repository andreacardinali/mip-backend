# Add this dataclass to market_data/models.py. The package installer script does this automatically.
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class IndexConstituent:
    index_code: str
    symbol: str
    name: str | None = None
    exchange_code: str | None = None
    sector: str | None = None
    industry: str | None = None
    source_reference: str | None = None
    source: str | None = None
    raw_payload: dict[str, Any] = field(default_factory=dict)
