import json
from typing import Any

from http_client import RetryPolicy, create_http_client, request_with_retry
from llm.base import LLMProvider, LLMRequest, LLMResponse
from llm.json_config import deep_merge_dicts, validate_protected_paths
from llm.parsing import parse_json_object

PROTECTED_PATHS = frozenset(
    {
        "model",
        "stream",
        "think",
        "format",
        "prompt",
        "options.temperature",
        "options.num_predict",
    }
)


class OllamaProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        model: str,
        timeout: float = 120,
        max_retries: int = 4,
        think: bool = False,
        json_config: dict[str, Any] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.retry_policy = RetryPolicy(max_retries=max_retries)
        self.max_retries = max_retries
        self.think = bool(think)
        self.json_config = dict(json_config or {})

    def generate_json(self, request: LLMRequest) -> LLMResponse:
        response_format = request.response_schema or "json"
        prompt = request.prompt
        if request.response_schema is not None:
            prompt = (
                f"{prompt}\n\nReturn JSON conforming exactly to this JSON Schema:\n"
                f"{json.dumps(request.response_schema, separators=(',', ':'))}"
            )
        standard_body: dict[str, Any] = {
            "model": self.model,
            "stream": False,
            "think": self.think,
            "format": response_format,
            "prompt": prompt,
            "options": {
                "temperature": request.temperature,
                "num_predict": request.max_tokens,
            },
        }
        body = deep_merge_dicts(standard_body, self.json_config)
        validate_protected_paths(body, standard_body, PROTECTED_PATHS)
        with create_http_client(timeout=self.timeout) as client:
            response = request_with_retry(
                client,
                "POST",
                f"{self.base_url}/api/generate",
                policy=self.retry_policy,
                retry_non_idempotent=True,
                json=body,
            )
            raw = response.json()
        return LLMResponse(data=parse_json_object(raw.get("response", "")), raw=raw)
