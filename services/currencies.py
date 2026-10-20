from dataclasses import dataclass

from market_data.models import ExchangeRateSnapshot
from market_data.service import MarketDataService


@dataclass(frozen=True, slots=True)
class CurrencyCatalog:
    provider: str
    reference_date: str
    base_currency: str
    currencies: tuple[str, ...]

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "as_of": self.reference_date,
            "base_currency": self.base_currency,
            "currencies": [{"code": code} for code in self.currencies],
        }


def get_currency_catalog(
    market_data_service: MarketDataService,
) -> CurrencyCatalog:
    snapshot = market_data_service.get_exchange_rates()
    return currency_catalog_from_snapshot(snapshot)


def currency_catalog_from_snapshot(
    snapshot: ExchangeRateSnapshot,
) -> CurrencyCatalog:
    codes = {
        str(snapshot.base_currency or "").strip().upper(),
        *(
            str(code or "").strip().upper()
            for code, rate in snapshot.rates_to_usd.items()
            if rate is not None and rate > 0
        ),
    }
    currencies = tuple(
        sorted(code for code in codes if len(code) == 3 and code.isalpha())
    )
    return CurrencyCatalog(
        provider=str(snapshot.source or "").strip().lower(),
        reference_date=snapshot.reference_date.isoformat(),
        base_currency=str(snapshot.base_currency).strip().upper(),
        currencies=currencies,
    )


def normalize_supported_currency(
    value: object,
    market_data_service: MarketDataService,
) -> str:
    currency = str(value or "").strip().upper()
    catalog = get_currency_catalog(market_data_service)
    if currency not in catalog.currencies:
        raise ValueError(f"Unsupported currency: {currency or '<empty>'}")
    return currency
