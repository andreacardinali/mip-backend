from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from typing import Any


class PayloadValidationError(ValueError):
    pass


def _required_text(payload: Mapping[str, Any], key: str) -> str:
    value = str(payload.get(key) or "").strip()
    if not value:
        raise PayloadValidationError(f"missing or empty {key}")
    return value


def _confidence(payload: Mapping[str, Any]) -> int:
    try:
        value = int(payload.get("eventConfidence", 0))
    except (TypeError, ValueError) as exc:
        raise PayloadValidationError("invalid eventConfidence") from exc
    if not 0 <= value <= 100:
        raise PayloadValidationError("eventConfidence must be between 0 and 100")
    return value


@dataclass(frozen=True, slots=True)
class UpcomingEvent:
    payload: dict[str, Any]
    event_type: str
    event_date: date
    confidence: int


@dataclass(frozen=True, slots=True)
class BreakingEvent:
    payload: dict[str, Any]
    event_type: str
    event_day: str
    fingerprint: str
    confidence: int


@dataclass(frozen=True, slots=True)
class DailyValidation:
    payload: dict[str, Any]
    source_indices: list[int]


def parse_upcoming_event(payload: Mapping[str, Any]) -> UpcomingEvent:
    event_type = _required_text(payload, "eventType")
    raw_date = _required_text(payload, "eventDate")
    try:
        event_date = date.fromisoformat(raw_date[:10])
    except ValueError as exc:
        raise PayloadValidationError("invalid eventDate") from exc
    confidence = _confidence(payload)
    normalized = dict(payload)
    normalized.update(
        eventType=event_type,
        eventDate=event_date.isoformat(),
        eventConfidence=confidence,
    )
    return UpcomingEvent(
        payload=normalized,
        event_type=event_type,
        event_date=event_date,
        confidence=confidence,
    )


def parse_breaking_event(payload: Mapping[str, Any]) -> BreakingEvent:
    event_type = _required_text(payload, "eventType")
    raw_date = _required_text(payload, "eventDate")
    try:
        event_day = date.fromisoformat(raw_date[:10]).isoformat()
    except ValueError as exc:
        raise PayloadValidationError("invalid eventDate") from exc
    confidence = _confidence(payload)
    normalized = dict(payload)
    normalized.update(
        eventType=event_type,
        eventDate=event_day,
        eventConfidence=confidence,
    )
    return BreakingEvent(
        payload=normalized,
        event_type=event_type,
        event_day=event_day,
        fingerprint=_required_text(payload, "eventFingerprint"),
        confidence=confidence,
    )


def parse_daily_validation(
    payload: Mapping[str, Any],
    *,
    expected_symbol: str,
) -> DailyValidation:
    symbol = _required_text(payload, "symbol")
    if symbol != expected_symbol:
        raise PayloadValidationError(
            f"symbol mismatch: expected {expected_symbol}, got {symbol}"
        )

    normalized = dict(payload)
    cause_found = bool(normalized.get("causeFound"))
    raw_indices = normalized.get("sourceIndicesUsed") or []
    indices = [
        value
        for value in raw_indices
        if isinstance(value, int) and not isinstance(value, bool)
    ]
    if not cause_found:
        normalized.update(
            {
                "causeConfidence": 0,
                "likelyCause": None,
                "sourceIndicesUsed": [],
            }
        )
        indices = []
    return DailyValidation(
        payload=normalized,
        source_indices=indices,
    )
