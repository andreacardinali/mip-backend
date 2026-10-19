from dataclasses import dataclass

from sqlalchemy.orm import Session

from jobs.portfolio_intelligence.config import IntelligenceConfig
from llm.base import LLMProvider
from market_data.service import MarketDataService
from notifications.queue import NotificationQueue


@dataclass
class JobContext:
    db: Session
    config: IntelligenceConfig
    research_llm: LLMProvider
    validation_llm: LLMProvider
    market_data: MarketDataService
    notification_queue: NotificationQueue
