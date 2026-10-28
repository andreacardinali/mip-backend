from sqlalchemy import text
from sqlalchemy.orm import Session

from market_time.models import ExchangeHours


class SQLExchangeHoursRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_exchange(self, exchange: str) -> ExchangeHours | None:
        row = self.db.execute(
            text(
                "SELECT exchange, calendar_name, timezone_name "
                "FROM public.exchanges WHERE exchange = :exchange"
            ),
            {"exchange": exchange},
        ).mappings().one_or_none()
        return ExchangeHours(**row) if row is not None else None