from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class TemporalNormalizationError(ValueError):
    pass


def as_utc(value: datetime, *, source_timezone: str | None = None) -> datetime:
    if not isinstance(value, datetime):
        raise TemporalNormalizationError("An instant must be a datetime, not a date")
    if value.tzinfo is not None and value.utcoffset() is not None:
        return value.astimezone(UTC)
    if source_timezone is None:
        raise TemporalNormalizationError("Naive timestamps require a source timezone")

    try:
        zone = UTC if source_timezone == "UTC" else ZoneInfo(source_timezone)
    except ZoneInfoNotFoundError as exc:
        raise TemporalNormalizationError(
            f"Timezone unavailable: {source_timezone}; install tzdata on Windows"
        ) from exc
    candidates = set()
    for fold in (0, 1):
        candidate = value.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if candidate.astimezone(zone).replace(tzinfo=None) == value:
            candidates.add(candidate)
    if len(candidates) != 1:
        raise TemporalNormalizationError("Ambiguous or nonexistent local timestamp")
    return candidates.pop()