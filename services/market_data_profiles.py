from typing import Any

from market_data.capabilities import InstrumentType
from market_data.factory import market_data
from market_data.models import InstrumentReference


def normalize_instrument_type(value: object) -> InstrumentType:
    try:
        return InstrumentType(str(value or "UNKNOWN").strip().upper())
    except ValueError:
        return InstrumentType.UNKNOWN


def profile_payload(
    symbol: str,
    *,
    isin: str | None = None,
    instrument_type: object = None,
    currency: str | None = None,
    exchange_code: str | None = None,
    include_quote: bool = True,
) -> dict[str, Any] | None:
    profile = market_data.get_profile(
        InstrumentReference(
            symbol=symbol,
            isin=isin,
            instrument_type=normalize_instrument_type(instrument_type),
            currency=currency,
            exchange_code=exchange_code,
        ),
        required_fields={"name"},
        include_quote=include_quote,
    )
    return profile.to_import_payload() if profile else None
