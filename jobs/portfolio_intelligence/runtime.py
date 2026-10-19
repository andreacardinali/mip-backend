from config.settings import settings
from db import SessionLocal
from jobs.portfolio_intelligence.config import IntelligenceConfig
from jobs.portfolio_intelligence.context import JobContext
from llm.factory import create_provider
from market_data.config import MarketDataConfig
from market_data.factory import create_market_data_service
from notifications.queue import NotificationQueue


def build_context(db) -> JobContext:
    config = IntelligenceConfig.load()
    market_data_config = MarketDataConfig.from_settings(settings)

    return JobContext(
        db=db,
        config=config,
        research_llm=create_provider(config.research_llm),
        validation_llm=create_provider(config.validation_llm),
        market_data=create_market_data_service(market_data_config),
        notification_queue=NotificationQueue(
            db,
            enabled=config.create_notifications,
        ),
    )


def run_branch(branch) -> dict:
    db = SessionLocal()
    try:
        context = build_context(db)
        result = branch.execute(context)
        db.commit()
        return result.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
