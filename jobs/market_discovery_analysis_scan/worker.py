import logging
from typing import Any

from config.settings import settings
from job_runtime.database import run_transactional
from market_analysis.quant_prefilter import QuantPrefilterConfig
from market_analysis.scan import ScanConfig
from market_data.factory import market_data
from services.market_discovery_analysis_scan import (
    run_market_discovery_analysis_scan,
)

logger = logging.getLogger(__name__)


def _compact_result(result: dict[str, Any]) -> dict[str, Any]:
    """Return the stable, concise job fields suitable for INFO and CLI output."""
    return {
        "status": result.get("status"),
        "run_id": result.get("run_id"),
        "eligible_candidates": result.get("eligible_candidates", 0),
        "processed": result.get("processed", 0),
        "technical_candidates": result.get("technical_candidates", 0),
        "quant_candidates": result.get("quant_candidates", 0),
        "persisted_signals": result.get("persisted_signals", 0),
        "failures": result.get("failures", 0),
        "skipped_by_reason": result.get("skipped_by_reason", {}),
        "symbols": result.get("symbols", []),
    }


def run() -> dict[str, Any]:
    scan_config = ScanConfig(
        period=str(settings.get("MARKET_ANALYSIS_PERIOD", "1y")),
        interval=str(settings.get("MARKET_ANALYSIS_INTERVAL", "1d")),
        minimum_bars=int(
            settings.get("MARKET_ANALYSIS_MINIMUM_BARS", 200)
        ),
        timeframe=str(settings.get("MARKET_ANALYSIS_TIMEFRAME", "1D")),
        algorithm_version=str(
            settings.get(
                "MARKET_ANALYSIS_ALGORITHM_VERSION",
                "market-analysis-v2-discovery",
            )
        ),
    )
    quant_config = QuantPrefilterConfig(
        minimum_score=float(
            settings.get("MARKET_ANALYSIS_QUANT_MIN_SCORE", 0)
        ),
        limit=int(
            settings.get("MARKET_ANALYSIS_QUANT_CANDIDATE_LIMIT", 25)
        ),
    )
    result = run_transactional(
        lambda db: run_market_discovery_analysis_scan(
            db,
            market_data_service=market_data,
            scan_config=scan_config,
            quant_config=quant_config,
            core_min_attention_score=float(
                settings.get(
                    "MARKET_ANALYSIS_CORE_MIN_ATTENTION_SCORE",
                    20,
                )
            ),
            discovery_min_attention_score=float(
                settings.get(
                    "MARKET_ANALYSIS_DISCOVERY_MIN_ATTENTION_SCORE",
                    40,
                )
            ),
            core_limit=int(
                settings.get(
                    "MARKET_ANALYSIS_CORE_CANDIDATE_LIMIT",
                    100,
                )
            ),
            discovery_limit=int(
                settings.get(
                    "MARKET_ANALYSIS_DISCOVERY_CANDIDATE_LIMIT",
                    25,
                )
            ),
        )
    )

    logger.info(
        "Market discovery analysis scan completed summary=%s",
        _compact_result(result),
    )
    logger.debug(
        "Market discovery analysis scan completed details=%s",
        result,
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(_compact_result(run()))
