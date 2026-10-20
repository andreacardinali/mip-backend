from market_data.capabilities import InstrumentType
from services.entities import _instrument_type


def test_unknown_instrument_type_is_normalized():
    assert _instrument_type("not-known") == InstrumentType.UNKNOWN


def test_etf_instrument_type_is_preserved():
    assert _instrument_type("ETF") == InstrumentType.ETF
