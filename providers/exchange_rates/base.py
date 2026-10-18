from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol


@dataclass(frozen=True, slots=True)
class ExchangeRateSnapshot:
    source: str
    reference_date: date
    base_currency: str
    rates_to_usd: dict[str, Decimal]


class ExchangeRateProvider(Protocol):
    def get_latest_rates(self) -> ExchangeRateSnapshot:
        ...
