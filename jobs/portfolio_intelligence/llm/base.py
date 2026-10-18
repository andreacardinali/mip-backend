from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any
@dataclass
class LLMRequest:
    prompt: str
    temperature: float = 0.0
    max_tokens: int = 4096
    grounded_search: bool = False
    response_schema: dict[str, Any] | None = None
@dataclass
class LLMResponse:
    data: dict[str, Any]
    raw: dict[str, Any]
    sources: list[dict[str, Any]]
    search_executed: bool
class LLMProvider(ABC):
    @abstractmethod
    def generate_json(self, request: LLMRequest) -> LLMResponse: ...
