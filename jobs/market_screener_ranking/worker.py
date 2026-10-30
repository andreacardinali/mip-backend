from __future__ import annotations

import logging
from typing import Any

from job_runtime.database import run_transactional
from market_screener.config import ScreenerRankingConfig
from market_screener.repository import MarketScreenerRepository
from market_screener.service import build_market_screener_ranking
from market_screener.coordination import run_coordinated_analysis

logger = logging.getLogger(__name__)


def run() -> dict[str, Any]:
    return run_coordinated_analysis(_run_ranking)


def _run_ranking() -> dict[str, Any]:
    result = run_transactional(
        lambda db: build_market_screener_ranking(
            MarketScreenerRepository(db), config=ScreenerRankingConfig()
        )
    )
    logger.info(
        "Market screener ranking completed summary=%s",
        {
            "status": result["status"],
            "input_signals": result["input_signals"],
            "ranked_opportunities": result["ranked_opportunities"],
        },
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
