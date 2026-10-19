import httpx

from http_client.retry import RetryPolicy, request_with_retry
from llm.base import LLMProvider, LLMRequest, LLMResponse
from llm.parsing import parse_json_object


class GeminiProvider(LLMProvider):
    def __init__(
        self, api_key: str, model: str, timeout: float = 60, max_retries: int = 4
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.retry_policy = RetryPolicy(max_retries=max_retries)
        self.max_retries = max_retries

    def generate_json(self, request: LLMRequest) -> LLMResponse:
        url = (
            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{self.model}:generateContent"
        )
        body = {
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
                "responseMimeType": "application/json",
            },
        }
        if request.response_schema:
            body["generationConfig"]["responseSchema"] = request.response_schema
        if request.grounded_search:
            body["tools"] = [{"google_search": {}}]

        with httpx.Client(timeout=self.timeout) as client:
            response = request_with_retry(
                client,
                "POST",
                url,
                policy=self.retry_policy,
                retry_non_idempotent=True,
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )
            raw = response.json()

        candidate = (raw.get("candidates") or [None])[0]
        if not candidate:
            raise RuntimeError("Gemini returned no candidate")
        finish_reason = candidate.get("finishReason")
        if finish_reason not in {None, "STOP"}:
            raise RuntimeError(f"Gemini incomplete response: {finish_reason}")
        parts = [
            part
            for part in candidate.get("content", {}).get("parts", [])
            if not part.get("thought") and part.get("text")
        ]
        data = parse_json_object("\n".join(part["text"] for part in parts))
        metadata = candidate.get("groundingMetadata") or {}
        sources = []
        for index, chunk in enumerate(metadata.get("groundingChunks") or []):
            web = chunk.get("web") or {}
            if web.get("uri") or web.get("title"):
                sources.append(
                    {
                        "sourceIndex": index,
                        "title": web.get("title"),
                        "uri": web.get("uri"),
                    }
                )
        return LLMResponse(
            data=data,
            raw=raw,
            sources=sources,
            search_executed=bool(metadata.get("webSearchQueries") or sources),
        )
