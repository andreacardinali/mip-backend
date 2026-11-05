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
            members=[
                SimpleNamespace(symbol="FIRST", instrument_id="FIRST"),
                SimpleNamespace(symbol="SECOND", instrument_id="SECOND"),
            ],
            config=ScanConfig(instrument_batch_size=1),
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
        config=ScanConfig(instrument_batch_size=1),
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


def test_lake_preserves_no_signal_evaluations_without_run_in_identity(monkeypatch):
    from datetime import UTC, datetime
    from unittest.mock import Mock
    from uuid import uuid4

    from market_analysis.quant_prefilter import QuantPrefilterConfig
    from market_analysis.scan import ScanEvaluation, ScanMember, ScanResult
    from services import market_analysis_lake as lake

    envelopes = []
    identifiers = [str(uuid4()), str(uuid4())]

    def save(db, envelope, *, lake):
        assert lake == "analytical"
        envelopes.append(envelope.as_payload())
        return identifiers[(len(envelopes) - 1) % 2]

    monkeypatch.setattr(lake, "save_event", save)
    member = ScanMember("u", "i", "e", "ABC", "X", "USD", None)
    result = ScanResult(
        processed=1,
        candidates=(),
        failures=(),
        evaluations=(
            ScanEvaluation(
                "i",
                datetime(2026, 11, 1, tzinfo=UTC),
                ("TECH",),
                "1D",
                {"market_price": 12},
                "a" * 64,
                200,
            ),
        ),
    )
    repository = Mock()
    for run_id in ("first-run", "second-run"):
        assert lake.save_scan_evaluations(
            object(),
            repository,
            run_id=run_id,
            members=[member],
            result=result,
            config=ScanConfig(),
            quant_config=QuantPrefilterConfig(),
        ) == {"i": identifiers[1]}
    assert envelopes[:2] == envelopes[2:]
    assert envelopes[1]["payload"]["outcome"] == "NO_SIGNAL"
    assert envelopes[1]["parent_event_ids"] == [identifiers[0]]
    assert envelopes[0]["subjects"] == [{"instrument_id": "i", "entity_id": "e"}]
    assert repository.link_scan_event.call_count == 4


def test_lake_observation_is_independent_of_strategy_selection(monkeypatch):
    from dataclasses import replace
    from datetime import UTC, datetime
    from unittest.mock import Mock
    from uuid import uuid4

    from market_analysis.quant_prefilter import QuantPrefilterConfig
    from market_analysis.scan import ScanEvaluation, ScanMember, ScanResult
    from services import market_analysis_lake as lake

    envelopes = []

    def save(db, envelope, *, lake):
        assert lake == "analytical"
        envelopes.append(envelope.as_payload())
        return str(uuid4())

    monkeypatch.setattr(lake, "save_event", save)
    member = ScanMember("u", "i", None, "ABC")
    evaluation = ScanEvaluation(
        "i",
        datetime(2026, 11, 1, tzinfo=UTC),
        ("TECH", "SWING"),
        "1D",
        {"market_price": 12},
        "a" * 64,
        200,
    )
    for strategies in (("TECH", "SWING"), ("SWING", "TECH"), ("TECH",)):
        lake.save_scan_evaluations(
            object(),
            Mock(),
            run_id="run",
            members=[member],
            result=ScanResult(
                processed=1,
                candidates=(),
                failures=(),
                evaluations=(replace(evaluation, strategy_codes=strategies),),
            ),
            config=ScanConfig(enabled_strategies=strategies),
            quant_config=QuantPrefilterConfig(),
        )
    assert envelopes[0] == envelopes[2] == envelopes[4]
    assert envelopes[1]["event_key"] == envelopes[3]["event_key"]
    assert envelopes[1]["payload"] == envelopes[3]["payload"]
    assert envelopes[1]["event_key"] != envelopes[5]["event_key"]
