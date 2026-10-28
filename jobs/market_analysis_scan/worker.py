import logging

from config.settings import settings
from job_runtime.database import run_transactional
from market_analysis.scan import ScanConfig
from market_data.factory import market_data
from services.market_analysis_scan import run_market_analysis_scan

logger = logging.getLogger(__name__)


def run() -> dict:
    universe_code = str(settings.get("MARKET_ANALYSIS_UNIVERSE", "NASDAQ100"))
    config = ScanConfig.from_settings(settings)
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
