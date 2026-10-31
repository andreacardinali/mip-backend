import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any
from uuid import UUID

from market_time.normalization import as_utc


def json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return as_utc(value).isoformat()
    if isinstance(value, (date, UUID)):
        return str(value)
    if isinstance(value, dict):
        return {key: json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    return value


def stable_event_key(*parts: Any) -> str:
    encoded = json.dumps(json_value(parts), sort_keys=True, allow_nan=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EventEnvelope:
    event_type: str
    stage: str
    source: str
    event_key: str
    payload: dict[str, Any]
    subjects: list[dict[str, Any]] = field(default_factory=list)
    schema_version: int = 1
    payload_version: int = 1
    source_event_id: str | None = None
    occurred_at: datetime | None = None
    event_day: date | None = None
    observed_at: datetime | None = None
    parent_event_ids: list[str] = field(default_factory=list)
    raw_payload: dict[str, Any] | None = None
    evidence: dict[str, Any] = field(default_factory=dict)
    provenance: dict[str, Any] = field(default_factory=dict)

    def as_payload(self) -> dict[str, Any]:
        for name in ("event_type", "source", "event_key"):
            if (
                not isinstance(getattr(self, name), str)
                or not getattr(self, name).strip()
            ):
                raise ValueError(f"Event requires {name}")
        if not isinstance(self.stage, str) or self.stage not in {
            "ACQUIRED",
            "ENRICHED",
            "VALIDATED",
        }:
            raise ValueError("Invalid event stage")
        if (
            type(self.schema_version) is not int
            or self.schema_version != 1
            or type(self.payload_version) is not int
            or self.payload_version < 1
        ):
            raise ValueError("Unsupported event version")
        for name in ("payload", "evidence", "provenance"):
            if not isinstance(getattr(self, name), dict):
                raise ValueError(f"Event {name} must be an object")
        if self.raw_payload is not None and not isinstance(self.raw_payload, dict):
            raise ValueError("Event raw_payload must be an object")
        if not isinstance(self.subjects, list) or not all(
            isinstance(subject, dict) for subject in self.subjects
        ):
            raise ValueError("Event subjects must be a list of objects")
        if self.event_day is not None and type(self.event_day) is not date:
            raise ValueError("Event event_day must be a date, not an instant")
        for name in ("occurred_at", "observed_at"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, datetime):
                raise ValueError(f"Event {name} must be an aware datetime")
        if self.source_event_id is not None and not isinstance(
            self.source_event_id, str
        ):
            raise ValueError("Event source_event_id must be text")
        if not isinstance(self.parent_event_ids, list):
            raise ValueError("Event parent_event_ids must be a list")
        for parent in self.parent_event_ids:
            UUID(str(parent))
        result = json_value(vars(self))
        json.dumps(result, allow_nan=False)
        return result
