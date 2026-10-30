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
    assert captured["incremental"] is True


def test_incremental_scan_commits_each_member_before_loading_next(monkeypatch):
    from types import SimpleNamespace

    import pytest

    from market_analysis.scan import ScanResult
    from services import market_analysis_scan as service

    events = []

    class Repository:
        def __init__(self, db):
            pass

        def start_scan_run(self, **kwargs):
            return "run"

        def finish_scan_run(self, **kwargs):
            events.append(kwargs["status"])

    def scan(members, **kwargs):
        events.append(members[0].symbol)
        if members[0].symbol == "SECOND":
            assert events[-2] == "commit"
            raise RuntimeError("interrupted")
        return ScanResult(processed=1, candidates=(), failures=())

    monkeypatch.setattr(service, "MarketAnalysisRepository", Repository)
    monkeypatch.setattr(service, "scan_members", scan)
    monkeypatch.setattr(service, "expire_evaluated_signals", lambda *args: 0)
    db = SimpleNamespace(
        commit=lambda: events.append("commit"),
        rollback=lambda: events.append("rollback"),
    )
    with pytest.raises(RuntimeError, match="interrupted"):
        service.run_member_analysis_scan(
            db,
            market_data_service=object(),
            universe_id="universe",
            universe_code="context",
            members=[SimpleNamespace(symbol="FIRST"), SimpleNamespace(symbol="SECOND")],
            incremental=True,
        )
    assert events == [
        "commit",
        "FIRST",
        "commit",
        "SECOND",
        "rollback",
        "FAILED",
        "commit",
    ]


def test_incremental_scan_continues_after_member_persistence_failure(monkeypatch):
    from types import SimpleNamespace

    from market_analysis.scan import ScanResult
    from services import market_analysis_scan as service

    events = []
    failures = []

    class Repository:
        def __init__(self, db):
            pass

        def start_scan_run(self, **kwargs):
            return "run"

        def finish_scan_run(self, **kwargs):
            events.append(kwargs["status"])

        def record_scan_failure(self, **kwargs):
            failures.append(kwargs)

    def scan(members, **kwargs):
        events.append(members[0].symbol)
        return ScanResult(processed=1, candidates=(), failures=())

    def expire(*args):
        if events[-1] == "BAD":
            raise RuntimeError("write failed")
        return 1

    monkeypatch.setattr(service, "MarketAnalysisRepository", Repository)
    monkeypatch.setattr(service, "scan_members", scan)
    monkeypatch.setattr(service, "expire_evaluated_signals", expire)
    db = SimpleNamespace(
        commit=lambda: events.append("commit"),
        rollback=lambda: events.append("rollback"),
    )
    result = service.run_member_analysis_scan(
        db,
        market_data_service=object(),
        universe_id="universe",
        universe_code="context",
        members=[
            SimpleNamespace(symbol=symbol, instrument_id=symbol)
            for symbol in ("FIRST", "BAD", "THIRD")
        ],
        incremental=True,
    )
    assert result["status"] == "PARTIAL"
    assert result["failed_symbols"] == ["BAD"]
    assert result["expired_signals"] == 2
    assert failures[0]["symbol"] == "BAD"
    assert events == [
        "commit",
        "FIRST",
        "commit",
        "BAD",
        "rollback",
        "commit",
        "THIRD",
        "commit",
        "COMPLETED",
        "commit",
    ]
