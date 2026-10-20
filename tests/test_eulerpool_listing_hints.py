from market_data.providers.eulerpool import _listing_hints


def test_eur_paris_listing():
    assert _listing_hints("GOLD-EUR.PA") == ("EUR", "XPAR")


def test_london_listing():
    assert _listing_hints("GLDD.L") == ("GBP", "XLON")


def test_milan_listing():
    assert _listing_hints("WSPX.MI") == ("EUR", "XMIL")
