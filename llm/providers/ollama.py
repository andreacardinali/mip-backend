import httpx

from http_client.retry import RetryPolicy, request_with_retry
from llm.base import LLMProvider, LLMRequest, LLMResponse
from llm.parsing import parse_json_object


class OllamaProvider(LLMProvider):
    def __init__(
        self, base_url: str, model: str, timeout: float = 120, max_retries: int = 4
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.retry_policy = RetryPolicy(max_retries=max_retries)
        self.max_retries = max_retries

    def generate_json(self, request: LLMRequest) -> LLMResponse:
        with httpx.Client(timeout=self.timeout) as client:
            response = request_with_retry(
                client,
                "POST",
                f"{self.base_url}/api/generate",
                policy=self.retry_policy,
                retry_non_idempotent=True,
                json={
                    "model": self.model,
                    "stream": False,
                    "think": False,
                    "format": "json",
                    "prompt": request.prompt,
                    "options": {
                        "temperature": request.temperature,
                        "num_predict": request.max_tokens,
                    },
                },
            )
            raw = response.json()
        return LLMResponse(data=parse_json_object(raw.get("response", "")), raw=raw)
