from datetime import date
from decimal import Decimal

import pytest

from market_data.models import ExchangeRateSnapshot
from services.currencies import (
    currency_catalog_from_snapshot,
    normalize_supported_currency,
)


class FakeMarketDataService:
    def get_exchange_rates(self):
        return ExchangeRateSnapshot(
            source="TEST_FX",
            reference_date=date(2026, 10, 20),
            base_currency="EUR",
            rates_to_usd={
                "USD": Decimal("1"),
                "CHF": Decimal("1.262"),
            },
        )


def test_catalog_is_derived_from_exchange_rate_snapshot():
    catalog = currency_catalog_from_snapshot(
        FakeMarketDataService().get_exchange_rates()
    )
    assert catalog.provider == "test_fx"
    assert catalog.base_currency == "EUR"
    assert catalog.currencies == ("CHF", "EUR", "USD")


def test_currency_validation_uses_active_exchange_rate_provider():
    service = FakeMarketDataService()
    assert normalize_supported_currency("chf", service) == "CHF"
    with pytest.raises(ValueError, match="Unsupported currency"):
        normalize_supported_currency("GBP", service)
