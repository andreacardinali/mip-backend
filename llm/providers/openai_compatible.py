import httpx

from http_client.retry import RetryDelayStrategy, RetryPolicy, request_with_retry
from llm.base import LLMProvider, LLMRequest, LLMResponse
from llm.parsing import parse_json_object
from llm.rate_limit import LLMRateLimiter, estimate_tokens
from llm.retry_hints import llm_response_delay


class OpenAICompatibleProvider(LLMProvider):
    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60,
        max_retries: int = 4,
        tokens_per_minute: int = 0,
        requests_per_minute: int = 0,
        rate_limit_safety_factor: float = 0.9,
        retry_max_delay: float = 120.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.retry_policy = RetryPolicy(
            max_retries=max_retries,
            max_delay_seconds=retry_max_delay,
            delay_strategy=RetryDelayStrategy.HYBRID,
            response_delay_resolver=llm_response_delay,
        )
        self.rate_limiter = LLMRateLimiter(
            tokens_per_minute=tokens_per_minute,
            requests_per_minute=requests_per_minute,
            safety_factor=rate_limit_safety_factor,
        )
        self.max_retries = max_retries

    def generate_json(self, request: LLMRequest) -> LLMResponse:
        self.rate_limiter.acquire(estimate_tokens(request.prompt, request.max_tokens))
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
            try:
                response = request_with_retry(
                    client,
                    "POST",
                    f"{self.base_url}/chat/completions",
                    policy=self.retry_policy,
                    retry_non_idempotent=True,
                    headers=headers,
                    json=body,
                )
            except httpx.HTTPStatusError as exc:
                delay = llm_response_delay(exc.response)
                if delay is not None:
                    self.rate_limiter.block(delay)
                raise RuntimeError(
                    "OpenAI-compatible endpoint returned "
                    f"HTTP {exc.response.status_code}: {exc.response.text}"
                ) from exc
            raw = response.json()

        choices = raw.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM returned no choices: {raw}")
        choice = choices[0]
        message = choice.get("message") or {}
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(
                "LLM returned no textual content: "
                f"finish_reason={choice.get('finish_reason')!r}, "
                f"message={message!r}, usage={raw.get('usage')!r}"
            )
        return LLMResponse(data=parse_json_object(text), raw=raw)
