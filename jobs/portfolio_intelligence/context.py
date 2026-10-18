from dataclasses import dataclass

from sqlalchemy.orm import Session

from jobs.portfolio_intelligence.config import IntelligenceConfig
from jobs.portfolio_intelligence.llm.base import LLMProvider
from notifications.queue import NotificationQueue


@dataclass
class JobContext:
    db: Session
    config: IntelligenceConfig
    research_llm: LLMProvider
    validation_llm: LLMProvider
    notification_queue: NotificationQueue
