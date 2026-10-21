from services.profile_persistence import pin_profile_to_listing


def test_pin_profile_preserves_selected_listing():
    original = {
        "symbol": "VWCE.DE",
        "exchange": "XETR",
        "currency": "EUR",
        "name": "Vanguard FTSE All-World UCITS ETF",
    }

    pinned = pin_profile_to_listing(
        original,
        symbol="VWCE.MI",
        exchange="XMIL",
        currency="EUR",
    )

    assert pinned["symbol"] == "VWCE.MI"
    assert pinned["exchange"] == "XMIL"
    assert pinned["currency"] == "EUR"
    assert original["symbol"] == "VWCE.DE"


def test_pin_profile_allows_database_exchange_normalization_when_missing():
    pinned = pin_profile_to_listing(
        {"symbol": "SPCX", "exchange": None, "currency": "EUR"},
        symbol="1SPCX.MI",
    )

    assert pinned["symbol"] == "1SPCX.MI"
    assert pinned["exchange"] is None
