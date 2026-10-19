from pathlib import Path

from config.settings import settings
from job_runtime.database import run_transactional
from jobs.portfolio_intelligence.config import IntelligenceConfig
from jobs.portfolio_intelligence.context import JobContext
from llm.factory import create_provider
from market_data.config import MarketDataConfig
from market_data.factory import create_market_data_service
from notifications.queue import NotificationQueue
from prompting.repository import FileSystemPromptRepository

PROMPTS_ROOT = Path(__file__).resolve().parent / "prompts"


def build_context(db) -> JobContext:
    config = IntelligenceConfig.load()
    return JobContext(
        db=db,
        config=config,
        research_llm=create_provider(config.research_llm),
        validation_llm=create_provider(config.validation_llm),
        market_data=create_market_data_service(
            MarketDataConfig.from_settings(settings)
        ),
        notification_queue=NotificationQueue(
            db,
            enabled=config.create_notifications,
        ),
        prompts=FileSystemPromptRepository(PROMPTS_ROOT),
    )


def run_branch(branch) -> dict:
    def operation(db):
        result = branch.execute(build_context(db))
        return result.to_dict()

    return run_transactional(operation)
