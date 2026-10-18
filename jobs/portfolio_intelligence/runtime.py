from db import SessionLocal
from jobs.portfolio_intelligence.config import IntelligenceConfig
from jobs.portfolio_intelligence.context import JobContext
from jobs.portfolio_intelligence.llm.factory import create_research_provider, create_validation_provider
from jobs.portfolio_intelligence.notifications.service import NotificationService
from jobs.portfolio_intelligence.notifications.telegram import TelegramNotifier
from jobs.portfolio_intelligence.repositories.notifications import NotificationRepository


def build_context(db) -> JobContext:
    config = IntelligenceConfig.load()
    repository = NotificationRepository(db)
    return JobContext(
        db=db,
        config=config,
        research_llm=create_research_provider(config),
        validation_llm=create_validation_provider(config),
        notifications=NotificationService(
            repository,
            TelegramNotifier(config.telegram_bot_token, config.telegram_chat_id, config.request_timeout),
            config.send_notifications,
        ),
    )


def run_branch(branch) -> dict:
    db = SessionLocal()
    try:
        context = build_context(db)
        result = branch.execute(context)
        context.notifications.repo.purge_notifications(context.config.notification_retention_days)
        db.commit()
        return result.to_dict()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
