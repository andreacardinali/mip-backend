import json

from sqlalchemy import text
from sqlalchemy.orm import Session

from market_time.holidays import HolidaySnapshot, normalize_holidays
from market_time.models import ExchangeHours


class SQLExchangeHoursRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_exchange(self, exchange: str) -> ExchangeHours | None:
        row = (
            self.db.execute(
                text(
                    "SELECT exchange, calendar_name, timezone_name "
                    "FROM public.exchanges WHERE exchange = :exchange"
                ),
                {"exchange": exchange},
            )
            .mappings()
            .one_or_none()
        )
        return ExchangeHours(**row) if row is not None else None


class SQLHolidayRepository:
    def __init__(self, db: Session):
        self.db = db

    def list_exchanges(self, source: str) -> list[dict[str, str | list[str]]]:
        active_systems = self.db.execute(
            text(
                "SELECT count(*) FROM public.external_systems "
                "WHERE lower(code) = :source AND active"
            ),
            {"source": source.lower()},
        ).scalar_one()
        if active_systems != 1:
            raise ValueError("Expected exactly one active external system")
        rows = (
            self.db.execute(
                text(
                    "SELECT e.exchange, e.exchangefullname, "
                    "ARRAY(SELECT DISTINCT h.exchange_code "
                    "FROM public.exchange_holiday_snapshots h "
                    "WHERE h.exchange = e.exchange AND h.source = :source "
                    "ORDER BY h.exchange_code) AS exchange_codes, "
                    "ARRAY(SELECT DISTINCT identifiers.identifier "
                    "FROM public.exchange_source_mappings m "
                    "JOIN public.external_systems s ON s.system_id = m.system_id "
                    "CROSS JOIN LATERAL (VALUES (m.source_exchange_code), "
                    "(m.source_exchange_name)) AS identifiers(identifier) "
                    "WHERE m.exchange = e.exchange AND lower(s.code) = :source "
                    "AND s.active AND identifiers.identifier IS NOT NULL "
                    "AND btrim(identifiers.identifier) <> '' "
                    "ORDER BY identifiers.identifier) AS source_identifiers "
                    "FROM public.exchanges e ORDER BY e.exchange"
                ),
                {"source": source.lower()},
            )
            .mappings()
            .all()
        )
        return [dict(row) for row in rows]

    def save(self, exchange: str, snapshot: HolidaySnapshot):
        return self.db.execute(
            text(
                "SELECT public.sp_store_exchange_holidays("
                ":exchange, :exchange_code, :source, :provider_exchange, "
                ":as_of, :updated_at, :fetched_at, CAST(:payload AS jsonb))"
            ),
            {
                "exchange": exchange,
                "exchange_code": snapshot.exchange_code,
                "source": snapshot.source,
                "provider_exchange": snapshot.provider_exchange,
                "as_of": snapshot.as_of,
                "updated_at": snapshot.updated_at,
                "fetched_at": snapshot.fetched_at,
                "payload": json.dumps(snapshot.raw_payload),
            },
        ).scalar_one()

    def latest(self, exchange_code: str, source: str) -> HolidaySnapshot | None:
        row = (
            self.db.execute(
                text(
                    "SELECT exchange_code, source, raw_payload, fetched_at "
                    "FROM public.exchange_holiday_snapshots "
                    "WHERE exchange_code = :exchange_code AND source = :source "
                    "ORDER BY provider_updated_at DESC LIMIT 1"
                ),
                {"exchange_code": exchange_code, "source": source.lower()},
            )
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return normalize_holidays(
            row["exchange_code"],
            row["raw_payload"],
            source=row["source"],
            fetched_at=row["fetched_at"],
        )
