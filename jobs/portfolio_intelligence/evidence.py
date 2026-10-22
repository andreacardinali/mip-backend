import logging
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from jobs.portfolio_intelligence.models import EntityCandidate
from market_data.capabilities import InstrumentType
from market_data.models import InstrumentReference

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class EvidenceDiagnostics:
    calls: dict[str, int] = field(default_factory=dict)
    failures: dict[str, int] = field(default_factory=dict)

    def record_call(self, capability: str) -> None:
        self.calls[capability] = self.calls.get(capability, 0) + 1

    def record_failure(self, capability: str) -> None:
        self.failures[capability] = self.failures.get(capability, 0) + 1

    def to_dict(self) -> dict[str, dict[str, int]]:
        return {"calls": dict(self.calls), "failures": dict(self.failures)}


@dataclass(frozen=True, slots=True)
class MarketEvidence:
    news: tuple[dict[str, Any], ...] = ()
    etf_holdings: tuple[dict[str, Any], ...] = ()
    etf_countries: tuple[dict[str, Any], ...] = ()
    etf_sectors: tuple[dict[str, Any], ...] = ()
    earnings: tuple[dict[str, Any], ...] = ()
    economic_events: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "news": list(self.news),
            "etfHoldings": list(self.etf_holdings),
            "etfCountries": list(self.etf_countries),
            "etfSectors": list(self.etf_sectors),
            "earnings": list(self.earnings),
            "economicEvents": list(self.economic_events),
        }


class MarketEvidenceCollector:
    def __init__(self, market_data, config) -> None:
        self.market_data = market_data
        self.config = config
        self.diagnostics = EvidenceDiagnostics()

    def common_economic_events(self) -> tuple[dict[str, Any], ...]:
        if not self.config.market_evidence_enabled:
            return ()
        values = self._best_effort(
            "economic_calendar",
            lambda: self.market_data.get_economic_calendar(
                limit=self.config.economic_event_limit
            ),
        )
        return _serialize_many(values)

    def for_breaking(
        self,
        candidate: EntityCandidate,
        *,
        economic_events: tuple[dict[str, Any], ...] = (),
    ) -> MarketEvidence:
        if not self.config.market_evidence_enabled:
            return MarketEvidence(economic_events=economic_events)
        news = self._best_effort(
            "news",
            lambda: self.market_data.get_news(
                instrument_reference(candidate),
                limit=self.config.news_limit,
            ),
        )
        return MarketEvidence(
            news=_serialize_many(news),
            economic_events=economic_events,
        )

    def for_daily_mover(
        self,
        candidate: EntityCandidate,
        *,
        economic_events: tuple[dict[str, Any], ...] = (),
    ) -> MarketEvidence:
        return self.for_breaking(candidate, economic_events=economic_events)

    def for_upcoming(
        self,
        candidate: EntityCandidate,
        start: date,
        end: date,
        *,
        economic_events: tuple[
            dict[str, Any],
            ...,
        ] = (),
    ) -> MarketEvidence:
        if not self.config.market_evidence_enabled:
            return MarketEvidence(
                economic_events=economic_events,
            )

        reference = instrument_reference(candidate)

        earnings: tuple[
            dict[str, Any],
            ...,
        ] = ()

        if reference.instrument_type == InstrumentType.STOCK:
            earnings = self._upcoming_earnings(
                candidate,
                start,
                end,
            )

        holdings: tuple[
            dict[str, Any],
            ...,
        ] = ()
        countries: tuple[
            dict[str, Any],
            ...,
        ] = ()
        sectors: tuple[
            dict[str, Any],
            ...,
        ] = ()

        if reference.instrument_type in {
            InstrumentType.ETF,
            InstrumentType.ETP,
        }:
            holdings = _serialize_many(
                self._best_effort(
                    "etf_holdings",
                    lambda: (
                        self.market_data.get_etf_holdings(
                            reference,
                            limit=(self.config.etf_holding_limit),
                        )
                    ),
                )
            )

            countries = _serialize_many(
                self._best_effort(
                    "etf_countries",
                    lambda: (self.market_data.get_etf_countries(reference)),
                )
            )

            sectors = _serialize_many(
                self._best_effort(
                    "etf_sectors",
                    lambda: (self.market_data.get_etf_sectors(reference)),
                )
            )

        return MarketEvidence(
            etf_holdings=holdings,
            etf_countries=countries,
            etf_sectors=sectors,
            earnings=earnings,
            economic_events=economic_events,
        )

    def _best_effort(self, capability: str, operation: Callable[[], Any]):
        self.diagnostics.record_call(capability)
        try:
            return operation() or []
        except Exception as exc:
            self.diagnostics.record_failure(capability)
            logger.info(
                "Portfolio intelligence evidence unavailable capability=%s error=%s",
                capability,
                exc,
            )
            return []

    def _upcoming_earnings(
        self,
        candidate: EntityCandidate,
        start: date,
        end: date,
    ) -> tuple[dict[str, Any], ...]:
        expected_symbol = candidate.symbol.strip().upper()

        earnings_by_key: dict[
            tuple[
                str,
                str,
                int | None,
                int | None,
            ],
            dict[str, Any],
        ] = {}

        current = start

        while current <= end:
            values = self._best_effort(
                "earnings_calendar",
                lambda event_date=current: (
                    self.market_data.get_earnings_calendar(
                        event_date,
                        symbol=candidate.symbol,
                    )
                ),
            )

            for value in values or []:
                serialized = _serialize(value)

                symbol = str(serialized.get("symbol") or "").strip().upper()

                if symbol != expected_symbol:
                    continue

                raw_event_date = str(serialized.get("event_date") or "").strip()

                try:
                    event_date = date.fromisoformat(raw_event_date)
                except ValueError:
                    continue

                if not start <= event_date <= end:
                    continue

                key = (
                    symbol,
                    event_date.isoformat(),
                    _optional_int(serialized.get("fiscal_year")),
                    _optional_int(serialized.get("fiscal_quarter")),
                )

                earnings_by_key[key] = serialized

            current += timedelta(days=1)

        ordered = sorted(
            earnings_by_key.values(),
            key=lambda item: (
                str(item.get("event_date") or ""),
                str(item.get("symbol") or ""),
                _optional_int(item.get("fiscal_year")) or 0,
                _optional_int(item.get("fiscal_quarter")) or 0,
            ),
        )

        return tuple(ordered)


def instrument_reference(candidate: EntityCandidate) -> InstrumentReference:
    return InstrumentReference(
        symbol=candidate.symbol,
        instrument_type=_instrument_type(candidate.instrument_type),
        currency=candidate.currency,
        exchange_code=candidate.exchange,
    )


def _instrument_type(value: str | None) -> InstrumentType:
    normalized = str(value or "UNKNOWN").strip().upper()
    aliases = {"MUTUAL_FUND": "FUND", "MUTUALFUND": "FUND"}
    normalized = aliases.get(normalized, normalized)
    try:
        return InstrumentType(normalized)
    except ValueError:
        return InstrumentType.UNKNOWN


def _serialize_many(values) -> tuple[dict[str, Any], ...]:
    return tuple(_serialize(item) for item in (values or []))


def _serialize(value: Any) -> dict[str, Any]:
    payload = asdict(value) if hasattr(value, "__dataclass_fields__") else dict(value)
    return {key: _json_value(item) for key, item in payload.items()}


def _json_value(value: Any):
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, tuple):
        return list(value)
    return value


def _optional_int(
    value: Any,
) -> int | None:
    if value is None or value == "":
        return None

    try:
        return int(value)
    except (TypeError, ValueError):
        return None
