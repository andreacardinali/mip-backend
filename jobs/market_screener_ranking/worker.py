from __future__ import annotations

import logging
from typing import Any

from config.settings import settings
from job_runtime.database import run_transactional
from market_screener.config import ScreenerRankingConfig
from market_screener.repository import MarketScreenerRepository
from market_screener.service import build_market_screener_ranking

logger = logging.getLogger(__name__)


def run() -> dict[str, Any]:
    config = ScreenerRankingConfig(
        attention_weight=float(settings.get("MARKET_SCREENER_ATTENTION_WEIGHT", 0.20)),
        technical_weight=float(settings.get("MARKET_SCREENER_TECHNICAL_WEIGHT", 0.25)),
        quant_weight=float(settings.get("MARKET_SCREENER_QUANT_WEIGHT", 0.35)),
        llm_weight=float(settings.get("MARKET_SCREENER_LLM_WEIGHT", 0.20)),
        minimum_score=float(settings.get("MARKET_SCREENER_MINIMUM_SCORE", 0)),
        limit=int(settings.get("MARKET_SCREENER_LIMIT", 50)),
    )
    algorithm_version = str(
        settings.get("MARKET_SCREENER_ALGORITHM_VERSION", "market-screener-v1")
    )
    signal_algorithm_version = (
        str(
            settings.get(
                "MARKET_SCREENER_SIGNAL_ALGORITHM_VERSION",
                "",
            )
        ).strip()
        or None
    )
    result = run_transactional(
        lambda db: build_market_screener_ranking(
            MarketScreenerRepository(db),
            config=config,
            algorithm_version=algorithm_version,
            signal_algorithm_version=signal_algorithm_version,
        )
    )
    logger.info(
        "Market screener ranking completed summary=%s",
        {
            "status": result["status"],
            "input_signals": result["input_signals"],
            "ranked_opportunities": result["ranked_opportunities"],
            "algorithm_version": result["algorithm_version"],
            "signal_algorithm_version": result["signal_algorithm_version"],
        },
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
