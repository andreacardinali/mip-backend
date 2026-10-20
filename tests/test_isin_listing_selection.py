from market_data.capabilities import InstrumentType
from market_data.providers.eulerpool import _looks_like_isin


def test_recognizes_isin_query():
    assert _looks_like_isin("FR0013416716") is True


def test_does_not_treat_symbol_as_isin():
    assert _looks_like_isin("GOLD-EUR.PA") is False


def test_listing_type_is_available():
    assert InstrumentType.ETF.value == "ETF"
