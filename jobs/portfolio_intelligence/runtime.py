from db import SessionLocal
from jobs.portfolio_intelligence.config import IntelligenceConfig
from jobs.portfolio_intelligence.context import JobContext
from jobs.portfolio_intelligence.llm.factory import (
    create_research_provider,
    create_validation_provider,
)
from notifications.queue import NotificationQueue


def build_context(db) -> JobContext:
    config = IntelligenceConfig.load()
    return JobContext(
        db=db,
        config=config,
        research_llm=create_research_provider(config),
        validation_llm=create_validation_provider(config),
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
