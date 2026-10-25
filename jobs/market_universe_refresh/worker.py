import logging

from config.settings import settings
from job_runtime.database import run_transactional
from market_analysis.universes import DEFAULT_UNIVERSES
from market_data.factory import market_data
from services.market_universe_incremental import (
    refresh_market_universes_incrementally,
)
from services.market_universe_instruments import import_universe_instrument

logger = logging.getLogger(__name__)


def run() -> dict:
    result = refresh_market_universes_incrementally(
        market_data_service=market_data,
        definitions=DEFAULT_UNIVERSES,
        run_transactional=run_transactional,
        instrument_importer=import_universe_instrument,
        import_limit=int(settings.get("MARKET_UNIVERSE_IMPORT_LIMIT", 0)),
    )
    logger.info("Market universe incremental refresh completed result=%s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
