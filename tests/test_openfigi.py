from providers.openfigi import OpenFigiProvider


def test_exact_figi_match_is_selected():
    source = {"symbol": "GOOG", "name": "Alphabet Inc.", "figi": "BBGTEST"}
    candidates = [
        {"figi": "OTHER", "ticker": "GOOG", "name": "OTHER"},
        {
            "figi": "BBGTEST",
            "ticker": "GOOG",
            "name": "ALPHABET INC",
            "exchCode": "US",
        },
    ]
    result = OpenFigiProvider._select_match(source, candidates)
    assert result["figi"] == "BBGTEST"


def test_composite_exchange_code_is_not_used_as_exchange():
    target = {"symbol": "GOOG", "exchange": None}
    OpenFigiProvider._merge(
        target,
        {"figi": "BBGTEST", "exchCode": "US", "micCode": None},
    )
    assert target["exchange"] is None
    assert target["openfigi_exch_code"] == "US"


def test_mic_is_used_as_exchange():
    target = {"symbol": "LDO.MI", "exchange": None}
    OpenFigiProvider._merge(
        target,
        {"figi": "BBGTEST", "exchCode": "IM", "micCode": "XMIL"},
    )
    assert target["exchange"] == "XMIL"
    assert target["mic_code"] == "XMIL"
