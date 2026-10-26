from __future__ import annotations

import logging
from typing import Any

from job_runtime.database import run_transactional
from market_screener.config import ScreenerRankingConfig
from market_screener.repository import MarketScreenerRepository
from market_screener.service import build_market_screener_ranking

logger = logging.getLogger(__name__)


def run() -> dict[str, Any]:
    result = run_transactional(
        lambda db: build_market_screener_ranking(
            MarketScreenerRepository(db),
            config=ScreenerRankingConfig(),
            # Compatibility with the current DB procedures. The application no
            # longer exposes or configures algorithm labels. A later schema
            # cleanup can remove these two legacy procedure arguments.
            algorithm_version="",
            signal_algorithm_version=None,
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
