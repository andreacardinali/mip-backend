from types import SimpleNamespace

from market_analysis.quant_prefilter import QuantPrefilterConfig
from market_analysis.scan import ScanConfig, ScanMember, ScanResult
from services.market_discovery_analysis_scan import (
    run_market_discovery_analysis_scan,
)


class EmptyDiscoveryRepository:
    def __init__(self, _db):
        pass

    def list_scan_candidates(self, **_kwargs):
        return []


def test_no_eligible_candidates_returns_skipped(monkeypatch):
    monkeypatch.setattr(
        "services.market_discovery_analysis_scan.DiscoveryScanRepository",
        EmptyDiscoveryRepository,
    )

    result = run_market_discovery_analysis_scan(
        object(),
        market_data_service=object(),
        scan_config=ScanConfig(),
        quant_config=QuantPrefilterConfig(),
    )

    assert result == {
        "status": "SKIPPED",
        "reason": "NO_ELIGIBLE_CANDIDATES",
        "run_id": None,
        "eligible_candidates": 0,
        "processed": 0,
        "technical_candidates": 0,
        "quant_candidates": 0,
        "persisted_signals": 0,
        "lifecycle_events": 0,
        "failures": 0,
        "failed_symbols": [],
        "skipped": 0,
        "skipped_by_reason": {},
        "skipped_symbols": [],
        "skipped_insufficient_history": [],
        "skipped_details": [],
        "signals": [],
    }


def test_quant_exclusion_does_not_prevent_signal_persistence(monkeypatch):
    persisted = []
    context = {
        "instrument_id": "instrument",
        "entity_id": "entity",
        "symbol": "AAPL",
        "universe_id": None,
        "candidate_id": "candidate",
        "snapshot_id": "snapshot",
        "origin": "DISCOVERY",
        "attention_score": 50,
        "universe_codes": [],
        "attention_evidence": {},
    }

    class DiscoveryRepository:
        def __init__(self, db):
            pass

        def list_scan_candidates(self, **kwargs):
            return [context]

    class Repository:
        def __init__(self, db):
            pass

        def upsert_universe(self, **kwargs):
            return "universe"

        def start_scan_run(self, **kwargs):
            return "run"

        def upsert_signal(self, **kwargs):
            persisted.append(kwargs)
            return "signal"

        def finish_scan_run(self, **kwargs):
            pass

    candidate = SimpleNamespace(
        strategy_code="TREND_PULLBACK",
        timeframe="1D",
        direction=SimpleNamespace(value="LONG"),
        signal_type=SimpleNamespace(value="BUY"),
        trend="BULLISH",
        reference_price=100,
        observed_at=None,
        confidence=0.7,
        reasons=(),
        evidence={},
        invalidation_price=None,
        target_price=None,
    )
    evaluated = SimpleNamespace(
        member=ScanMember("", "instrument", "entity", "AAPL"),
        candidate=candidate,
        scores=SimpleNamespace(technical=50, to_dict=lambda: {"technical": 50}),
    )
    monkeypatch.setattr(
        "services.market_discovery_analysis_scan.DiscoveryScanRepository",
        DiscoveryRepository,
    )
    monkeypatch.setattr(
        "services.market_discovery_analysis_scan.MarketAnalysisRepository",
        Repository,
    )
    monkeypatch.setattr(
        "services.market_discovery_analysis_scan.scan_members",
        lambda *args, **kwargs: ScanResult(1, (evaluated,), ()),
    )
    monkeypatch.setattr(
        "services.market_discovery_analysis_scan.reconcile_persisted_signals",
        lambda db, signal_ids, **kwargs: signal_ids,
    )
    monkeypatch.setattr(
        "services.market_discovery_analysis_scan.expire_evaluated_signals",
        lambda *args, **kwargs: 0,
    )

    result = run_market_discovery_analysis_scan(
        object(),
        market_data_service=object(),
        scan_config=ScanConfig(),
        quant_config=QuantPrefilterConfig(minimum_score=100),
    )

    assert result["quant_candidates"] == 0
    assert result["persisted_signals"] == 1
    assert result["lifecycle_events"] == 1
    assert persisted[0]["candidate"].strategy_code == "TREND_PULLBACK"
