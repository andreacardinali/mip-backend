import logging

from job_runtime.database import run_transactional
from market_data.factory import market_data
from services.news_ingestion import ingest_news

logger = logging.getLogger(__name__)


def run() -> dict:
    result = run_transactional(
        lambda db: ingest_news(db, market_data_service=market_data)
    )
    logger.info("News ingestion completed result=%s", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
