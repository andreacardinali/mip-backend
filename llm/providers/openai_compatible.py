import httpx

from http_client.retry import RetryPolicy, request_with_retry
from llm.base import LLMProvider, LLMRequest, LLMResponse
from llm.parsing import parse_json_object


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, base_url: str, api_key: str, model: str, timeout: float = 60, max_retries: int = 4) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.retry_policy = RetryPolicy(max_retries=max_retries)
        self.max_retries = max_retries

    def generate_json(self, request: LLMRequest) -> LLMResponse:
        body = {
            "model": self.model,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "response_format": {"type": "json_object"},
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        with httpx.Client(timeout=self.timeout) as client:
            response = request_with_retry(
                client,
                "POST",
                f"{self.base_url}/chat/completions",
                policy=self.retry_policy,
                retry_non_idempotent=True,
                headers=headers,
                json=body,
            )
            raw = response.json()
        choices = raw.get("choices") or []
        if not choices:
            raise RuntimeError("LLM returned no choices")
        text = choices[0].get("message", {}).get("content", "")
        return LLMResponse(data=parse_json_object(text), raw=raw)
