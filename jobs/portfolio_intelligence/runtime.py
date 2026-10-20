from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from sqlalchemy.orm import Session

from config.settings import settings
from job_runtime.database import run_transactional
from jobs.portfolio_intelligence.config import IntelligenceConfig
from jobs.portfolio_intelligence.models import BranchResult
from llm.base import LLMProvider
from llm.factory import create_provider
from market_data.config import MarketDataConfig
from market_data.factory import create_market_data_service
from market_data.service import MarketDataService
from notifications.queue import NotificationQueue
from prompting.repository import FileSystemPromptRepository

PROMPTS_ROOT = Path(__file__).resolve().parent / "prompts"


@dataclass(slots=True)
class IntelligenceContext:
    db: Session
    config: IntelligenceConfig
    research_llm: LLMProvider
    validation_llm: LLMProvider
    market_data: MarketDataService
    notification_queue: NotificationQueue
    prompts: FileSystemPromptRepository


class IntelligenceBranch(Protocol):
    name: str

    def execute(self, context: IntelligenceContext) -> BranchResult: ...


def build_context(db: Session) -> IntelligenceContext:
    config = IntelligenceConfig.load()
    return IntelligenceContext(
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


def run_branch(branch: IntelligenceBranch) -> dict:
    def operation(db: Session) -> dict:
        return branch.execute(build_context(db)).to_dict()

    return run_transactional(operation)
