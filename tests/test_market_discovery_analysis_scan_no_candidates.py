from market_analysis.quant_prefilter import QuantPrefilterConfig
from market_analysis.scan import ScanConfig
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
