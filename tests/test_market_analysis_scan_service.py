from market_analysis.scan import ScanConfig
from services.market_analysis_scan import run_market_analysis_scan


class FakeRepository:
    finished = []

    def __init__(self, db):
        pass

    def get_universe(self, *, code):
        return {"universe_id": "u", "code": code}

    def list_universe_members(self, *, universe_id):
        return []


def test_service_rejects_empty_universe(monkeypatch) -> None:
    monkeypatch.setattr(
        "services.market_analysis_scan.MarketAnalysisRepository", FakeRepository
    )
    try:
        run_market_analysis_scan(
            object(),
            market_data_service=object(),
            universe_code="EMPTY",
            config=ScanConfig(),
        )
    except ValueError as error:
        assert "no members" in str(error)
    else:
        raise AssertionError("expected ValueError")


def test_universe_delegates_to_shared_member_scan(monkeypatch) -> None:
    class MemberRepository(FakeRepository):
        def list_universe_members(self, *, universe_id):
            return [
                {
                    "universe_id": universe_id,
                    "instrument_id": "i",
                    "entity_id": "e",
                    "symbol": "AAPL",
                    "exchange": "NASDAQ",
                    "currency_code": "USD",
                    "isin": None,
                }
            ]

    captured = {}

    def member_scan(db, **kwargs):
        captured.update(kwargs)
        return {"status": "SUCCESS"}

    monkeypatch.setattr(
        "services.market_analysis_scan.MarketAnalysisRepository", MemberRepository
    )
    monkeypatch.setattr(
        "services.market_analysis_scan.run_member_analysis_scan", member_scan
    )
    assert run_market_analysis_scan(
        object(), market_data_service=object(), universe_code="NASDAQ100"
    ) == {"status": "SUCCESS"}
    assert captured["universe_id"] == "u"
    assert captured["members"][0].symbol == "AAPL"
