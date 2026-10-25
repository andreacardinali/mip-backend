import logging

from config.settings import settings
from job_runtime.database import run_transactional
from market_analysis.scan import ScanConfig
from market_data.factory import market_data
from services.market_analysis_scan import run_market_analysis_scan

logger = logging.getLogger(__name__)


def run() -> dict:
    universe_code = str(settings.get("MARKET_ANALYSIS_UNIVERSE", "NASDAQ100"))
    config = ScanConfig(
        period=str(settings.get("MARKET_ANALYSIS_PERIOD", "1y")),
        interval=str(settings.get("MARKET_ANALYSIS_INTERVAL", "1d")),
        minimum_bars=int(settings.get("MARKET_ANALYSIS_MINIMUM_BARS", 200)),
        timeframe=str(settings.get("MARKET_ANALYSIS_TIMEFRAME", "1D")),
        algorithm_version=str(
            settings.get("MARKET_ANALYSIS_ALGORITHM_VERSION", "market-analysis-v1")
        ),
    )
    result = run_transactional(
        lambda db: run_market_analysis_scan(
            db,
            market_data_service=market_data,
            universe_code=universe_code,
            config=config,
        )
    )
    logger.info("Market analysis scan completed result=%s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
