from dataclasses import dataclass
from sqlalchemy.orm import Session
from jobs.portfolio_intelligence.config import IntelligenceConfig
from jobs.portfolio_intelligence.llm.gemini import GeminiProvider
from jobs.portfolio_intelligence.llm.ollama import OllamaProvider
from jobs.portfolio_intelligence.notifications.service import NotificationService
from jobs.portfolio_intelligence.repositories.notifications import NotificationRepository
@dataclass
class JobContext:
    db: Session
    config: IntelligenceConfig
    gemini: GeminiProvider
    ollama: OllamaProvider
    notifications: NotificationService
