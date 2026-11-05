from typing import Any

from http_client import (
    HttpResponse,
    HttpStatusError,
    RetryDelayStrategy,
    RetryPolicy,
    create_http_client,
    request_with_retry,
)
from llm.base import LLMProvider, LLMRequest, LLMResponse
from llm.json_config import deep_merge_dicts, validate_protected_paths
from llm.parsing import parse_json_object
from llm.rate_limit import LLMRateLimiter, estimate_tokens
from llm.retry_hints import llm_response_delay

MAX_ERROR_BODY_CHARACTERS = 2000
PROTECTED_PATHS = frozenset(
    {
        "model",
        "messages",
        "input",
        "instructions",
        "max_tokens",
        "max_output_tokens",
    }
)
JSON_SYSTEM_PROMPT = (
    "Return exactly one valid JSON object. "
    "Do not include Markdown fences, XML tags, explanations, comments, "
    "or any text outside the JSON object. "
    "The first non-whitespace character must be '{' and the last "
    "non-whitespace character must be '}'."
)


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
        json_config: dict[str, Any] | None = None,
        api_protocol: str = "chat_completions",
    ) -> None:
        self.base_url = base_url.strip().rstrip("/")
        self.api_key = api_key.strip()
        self.model = model.strip()
        if not self.base_url:
            raise ValueError("base_url cannot be empty")
        if not self.model:
            raise ValueError("model cannot be empty")
        if api_protocol not in {"chat_completions", "responses"}:
            raise ValueError(
                f"Unsupported OpenAI-compatible API protocol: {api_protocol}"
            )
        self.api_protocol = api_protocol

        self.timeout = timeout
        self.json_config = dict(json_config or {})
        invalid_keys = PROTECTED_PATHS & self.json_config.keys()
        if invalid_keys:
            raise ValueError(
                "json_config cannot override: " + ", ".join(sorted(invalid_keys))
            )

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
        body = self._build_request_body(request)
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        with create_http_client(timeout=self.timeout) as client:
            try:
                response = request_with_retry(
                    client,
                    "POST",
                    f"{self.base_url}/{self._endpoint_path}",
                    policy=self.retry_policy,
                    retry_non_idempotent=True,
                    headers=headers,
                    json=body,
                )
            except HttpStatusError as exc:
                delay = llm_response_delay(exc.response)
                if delay is not None:
                    self.rate_limiter.block(delay)
                raise RuntimeError(
                    "OpenAI-compatible endpoint returned HTTP "
                    f"{exc.response.status_code}: "
                    f"{self._response_error_message(exc.response)}"
                ) from exc
            raw = self._decode_response(response)

        extract_text = (
            self._extract_responses_text
            if self.api_protocol == "responses"
            else self._extract_text
        )
        return LLMResponse(data=parse_json_object(extract_text(raw)), raw=raw)

    @property
    def _endpoint_path(self) -> str:
        if self.api_protocol == "responses":
            return "responses"
        return "chat/completions"

    def _build_request_body(self, request: LLMRequest) -> dict[str, Any]:
        if self.api_protocol == "responses":
            standard_body: dict[str, Any] = {
                "model": self.model,
                "instructions": JSON_SYSTEM_PROMPT,
                "input": request.prompt,
                "max_output_tokens": request.max_tokens,
            }
        else:
            standard_body = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": JSON_SYSTEM_PROMPT},
                    {"role": "user", "content": request.prompt},
                ],
                "max_tokens": request.max_tokens,
            }
        body = deep_merge_dicts(standard_body, self.json_config)
        validate_protected_paths(body, standard_body, PROTECTED_PATHS)
        return body

    @staticmethod
    def _decode_response(response: HttpResponse) -> dict[str, Any]:
        try:
            raw = response.json()
        except ValueError as exc:
            body = response.text.strip()
            if len(body) > MAX_ERROR_BODY_CHARACTERS:
                body = body[:MAX_ERROR_BODY_CHARACTERS] + "... [truncated]"
            raise RuntimeError(
                "OpenAI-compatible endpoint returned an invalid JSON response: "
                f"{body or '<empty response body>'}"
            ) from exc
        if not isinstance(raw, dict):
            raise RuntimeError(
                "OpenAI-compatible endpoint returned an unexpected response "
                f"type: {type(raw).__name__}"
            )
        return raw

    @staticmethod
    def _extract_text(raw: dict[str, Any]) -> str:
        choices = raw.get("choices") or []
        if not choices:
            raise RuntimeError(f"LLM returned no choices: {raw}")
        choice = choices[0]
        if not isinstance(choice, dict):
            raise RuntimeError(f"LLM returned an invalid choice: {choice!r}")
        message = choice.get("message") or {}
        if not isinstance(message, dict):
            raise RuntimeError(f"LLM returned an invalid message: {message!r}")
        text = message.get("content")
        if not isinstance(text, str) or not text.strip():
            raise RuntimeError(
                "LLM returned no textual content: "
                f"finish_reason={choice.get('finish_reason')!r}, "
                f"message={message!r}, usage={raw.get('usage')!r}"
            )
        return text

    @staticmethod
    def _extract_responses_text(raw: dict[str, Any]) -> str:
        output_text = raw.get("output_text")
        if isinstance(output_text, str) and output_text.strip():
            return output_text

        text_parts: list[str] = []
        for item in raw.get("output") or []:
            if not isinstance(item, dict):
                continue
            for content in item.get("content") or []:
                if (
                    isinstance(content, dict)
                    and content.get("type") in {"output_text", "text"}
                    and isinstance(content.get("text"), str)
                ):
                    text_parts.append(content["text"])
        text = "".join(text_parts)
        if not text.strip():
            raise RuntimeError(f"LLM returned no textual content: {raw}")
        return text

    @staticmethod
    def _response_error_message(response: HttpResponse) -> str:
        try:
            body = response.text.strip()
        except Exception as exc:
            return f"<unable to read response body: {type(exc).__name__}: {exc}>"
        if not body:
            return "<empty response body>"
        body = " ".join(body.split())
        if len(body) > MAX_ERROR_BODY_CHARACTERS:
            return body[:MAX_ERROR_BODY_CHARACTERS] + "... [truncated]"
        return body
