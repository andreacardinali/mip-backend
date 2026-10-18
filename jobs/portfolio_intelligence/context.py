from dataclasses import dataclass

from sqlalchemy.orm import Session

from jobs.portfolio_intelligence.config import (
    IntelligenceConfig,
)
from jobs.portfolio_intelligence.llm.base import (
    LLMProvider,
)
from jobs.portfolio_intelligence.notifications.service import (
    NotificationService,
)


@dataclass
class JobContext:
    db: Session
    config: IntelligenceConfig
    research_llm: LLMProvider
    validation_llm: LLMProvider
    notifications: NotificationService