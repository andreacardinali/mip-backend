import httpx

from jobs.portfolio_intelligence.llm.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
)
from jobs.portfolio_intelligence.llm.parsing import (
    parse_json_object,
)


class OpenAICompatibleProvider(
    LLMProvider
):

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        timeout: float = 60,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    def generate_json(
        self,
        request: LLMRequest,
    ) -> LLMResponse:

        url = (
            f"{self.base_url}/chat/completions"
        )

        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": request.prompt,
                }
            ],
            "temperature":
                request.temperature,
            "max_tokens":
                request.max_tokens,
            "response_format": {
                "type": "json_object",
            },
        }

        with httpx.Client(
            timeout=self.timeout
        ) as client:

            response = client.post(
                url,
                headers={
                    "Authorization":
                        f"Bearer {self.api_key}",
                    "Content-Type":
                        "application/json",
                },
                json=body,
            )

            response.raise_for_status()
            raw = response.json()

        choices = raw.get("choices") or []

        if not choices:
            raise RuntimeError(
                "LLM returned no choices"
            )

        text = (
            choices[0]
            .get("message", {})
            .get("content", "")
        )

        return LLMResponse(
            data=parse_json_object(text),
            raw=raw,
            sources=[],
            search_executed=False,
        )