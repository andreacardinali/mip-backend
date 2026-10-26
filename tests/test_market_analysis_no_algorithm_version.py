from __future__ import annotations

import inspect

from market_analysis.repository import MarketAnalysisRepository
from market_analysis.scan import ScanConfig
from services import market_analysis_scan as scan_service


def test_scan_config_and_repository_have_no_version_parameter() -> None:
    forbidden = "algorithm" + "_version"
    assert not hasattr(ScanConfig(), forbidden)
    assert (
        forbidden
        not in inspect.signature(MarketAnalysisRepository.start_scan_run).parameters
    )
    assert (
        forbidden
        not in inspect.signature(MarketAnalysisRepository.upsert_signal).parameters
    )
    assert forbidden not in inspect.getsource(scan_service.run_market_analysis_scan)
