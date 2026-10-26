from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True, slots=True)
class LLMRequest:
    prompt: str
    temperature: float = 0.0
    max_tokens: int = 4096
    grounded_search: bool = False
    response_schema: dict[str, Any] | None = None
    cache_namespace: str = ""
    cache_ttl_seconds: int | None = None
    cache_bypass: bool = False


@dataclass(frozen=True, slots=True)
class LLMResponse:
    data: dict[str, Any]
    raw: dict[str, Any]
    sources: list[dict[str, Any]] = field(default_factory=list)
    search_executed: bool = False


class LLMProvider(ABC):
    @abstractmethod
    def generate_json(self, request: LLMRequest) -> LLMResponse:
        raise NotImplementedError
