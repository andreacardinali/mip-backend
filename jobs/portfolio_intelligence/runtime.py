from db import SessionLocal
from jobs.portfolio_intelligence.config import IntelligenceConfig
from jobs.portfolio_intelligence.context import JobContext
from jobs.portfolio_intelligence.llm.gemini import GeminiProvider
from jobs.portfolio_intelligence.llm.ollama import OllamaProvider
from jobs.portfolio_intelligence.notifications.telegram import TelegramNotifier
from jobs.portfolio_intelligence.notifications.service import NotificationService
from jobs.portfolio_intelligence.repositories.notifications import NotificationRepository
from jobs.portfolio_intelligence.llm.factory import create_research_provider

def build_context(db):
    config=IntelligenceConfig.load(); repo=NotificationRepository(db)
    return JobContext(db=db,config=config,
        gemini=create_research_provider(config),
        ollama=OllamaProvider(config.ollama_base_url,config.ollama_model,max(config.request_timeout,120)),
        notifications=NotificationService(repo,TelegramNotifier(config.telegram_bot_token,config.telegram_chat_id,config.request_timeout),config.send_notifications))
def run_branch(branch):
    db=SessionLocal()
    try:
        context=build_context(db); result=branch.execute(context)
        context.notifications.repo.purge(context.config.notification_retention_days); db.commit()
        return result.to_dict()
    finally: db.close()
