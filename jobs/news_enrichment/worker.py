import logging

from db import SessionLocal
from services.news_enrichment import enrich_news

logger = logging.getLogger(__name__)


def run() -> dict:
    db = SessionLocal()
    try:
        result = enrich_news(db)
        logger.info("News enrichment completed result=%s", result)
        return result
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print(run())
