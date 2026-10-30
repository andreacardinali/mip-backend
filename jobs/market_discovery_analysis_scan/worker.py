import logging
from typing import Any

from config.settings import settings
from job_runtime.database import run_transactional
from market_analysis.quant_prefilter import QuantPrefilterConfig
from market_analysis.scan import ScanConfig
from market_data.factory import market_data
from market_screener.constants import (
    MARKET_ANALYSIS_CORE_CANDIDATE_LIMIT,
    MARKET_ANALYSIS_CORE_MIN_ATTENTION_SCORE,
    MARKET_ANALYSIS_DISCOVERY_CANDIDATE_LIMIT,
    MARKET_ANALYSIS_DISCOVERY_MIN_ATTENTION_SCORE,
    MARKET_ANALYSIS_QUANT_CANDIDATE_LIMIT,
    MARKET_ANALYSIS_QUANT_MIN_SCORE,
)
from services.market_discovery_analysis_scan import run_market_discovery_analysis_scan
from market_screener.coordination import run_coordinated_analysis

logger = logging.getLogger(__name__)


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": result.get("status"),
        "reason": result.get("reason"),
        "run_id": result.get("run_id"),
        "eligible_candidates": result.get("eligible_candidates", 0),
        "processed": result.get("processed", 0),
        "technical_candidates": result.get("technical_candidates", 0),
        "quant_candidates": result.get("quant_candidates", 0),
        "persisted_signals": result.get("persisted_signals", 0),
        "lifecycle_events": result.get("lifecycle_events", 0),
        "failures": result.get("failures", 0),
        "skipped_by_reason": result.get("skipped_by_reason", {}),
        "signals": result.get("signals", []),
    }


def run() -> dict[str, Any]:
    return run_coordinated_analysis(_run_scan)


def _run_scan() -> dict[str, Any]:
    scan_config = ScanConfig.from_settings(settings)
    quant_config = QuantPrefilterConfig(
        minimum_score=MARKET_ANALYSIS_QUANT_MIN_SCORE,
        limit=MARKET_ANALYSIS_QUANT_CANDIDATE_LIMIT,
    )
    result = run_transactional(
        lambda db: run_market_discovery_analysis_scan(
            db,
            market_data_service=market_data,
            scan_config=scan_config,
            quant_config=quant_config,
            core_min_attention_score=MARKET_ANALYSIS_CORE_MIN_ATTENTION_SCORE,
            discovery_min_attention_score=MARKET_ANALYSIS_DISCOVERY_MIN_ATTENTION_SCORE,
            core_limit=MARKET_ANALYSIS_CORE_CANDIDATE_LIMIT,
            discovery_limit=MARKET_ANALYSIS_DISCOVERY_CANDIDATE_LIMIT,
        )
    )
    logger.info(
        "Market discovery analysis scan %s summary=%s",
        "skipped" if result.get("status") == "SKIPPED" else "completed",
        _compact_result(result),
    )
    logger.debug("Market discovery analysis scan details=%s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(_compact_result(run()))
