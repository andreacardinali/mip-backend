from providers.openfigi import OpenFigiProvider


def test_exch_code_is_primary_exchange():
    target = {"symbol": "GOOG", "exchange": None}
    OpenFigiProvider._merge(target, {"figi": "BBGTEST", "exchCode": "US"})
    assert target["exchange"] == "US"
    assert target["openfigi_exch_code"] == "US"


def test_mic_is_not_required():
    assert (
        OpenFigiProvider._needs_enrichment(
            {"figi": "BBGTEST", "openfigi_exch_code": "US", "mic_code": None}
        )
        is False
    )
