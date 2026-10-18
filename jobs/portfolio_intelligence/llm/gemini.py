import random
import time

import httpx

from jobs.portfolio_intelligence.llm.base import (
    LLMProvider,
    LLMRequest,
    LLMResponse,
)
from jobs.portfolio_intelligence.llm.parsing import (
    parse_json_object,
)


class GeminiProvider(LLMProvider):

    RETRYABLE_STATUS_CODES = {
        408,
        429,
        500,
        502,
        503,
        504,
    }

    def __init__(
        self,
        api_key: str,
        model: str,
        timeout: float = 60,
        max_retries: int = 4,
    ):
        self.api_key = api_key
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries

    def _post_with_retry(
        self,
        client: httpx.Client,
        url: str,
        body: dict,
    ) -> httpx.Response:

        for attempt in range(
            self.max_retries + 1
        ):
            response = client.post(
                url,
                headers={
                    "x-goog-api-key": self.api_key,
                    "Content-Type": "application/json",
                },
                json=body,
            )

            if (
                response.status_code
                not in self.RETRYABLE_STATUS_CODES
            ):
                response.raise_for_status()
                return response

            if attempt >= self.max_retries:
                response.raise_for_status()

            retry_after = (
                response.headers.get("Retry-After")
            )

            if retry_after:
                try:
                    delay = float(retry_after)
                except ValueError:
                    delay = 2 ** attempt
            else:
                delay = 2 ** attempt

            delay += random.uniform(
                0.0,
                0.5,
            )

            time.sleep(delay)

        raise RuntimeError(
            "Gemini retry loop terminated unexpectedly"
        )

    def generate_json(
        self,
        request: LLMRequest,
    ) -> LLMResponse:

        url = (
            "https://generativelanguage.googleapis.com/"
            f"v1beta/models/{self.model}:generateContent"
        )

        body = {
            "contents": [
                {
                    "role": "user",
                    "parts": [
                        {
                            "text": request.prompt,
                        }
                    ],
                }
            ],
            "generationConfig": {
                "temperature":
                    request.temperature,
                "maxOutputTokens":
                    request.max_tokens,
                "responseMimeType":
                    "application/json",
            },
        }

        if request.response_schema:
            body["generationConfig"][
                "responseSchema"
            ] = request.response_schema

        if request.grounded_search:
            body["tools"] = [
                {
                    "google_search": {},
                }
            ]

        with httpx.Client(
            timeout=self.timeout
        ) as client:

            response = self._post_with_retry(
                client,
                url,
                body,
            )

            raw = response.json()

        candidate = (
            raw.get("candidates")
            or [None]
        )[0]

        if not candidate:
            raise RuntimeError(
                "Gemini returned no candidate"
            )

        finish_reason = candidate.get(
            "finishReason"
        )

        if finish_reason not in {
            None,
            "STOP",
        }:
            raise RuntimeError(
                "Gemini incomplete response: "
                f"{finish_reason}"
            )

        parts = [
            part
            for part in (
                candidate
                .get("content", {})
                .get("parts", [])
            )
            if (
                not part.get("thought")
                and part.get("text")
            )
        ]

        data = parse_json_object(
            "\n".join(
                part["text"]
                for part in parts
            )
        )

        metadata = (
            candidate.get(
                "groundingMetadata"
            )
            or {}
        )

        chunks = (
            metadata.get(
                "groundingChunks"
            )
            or []
        )

        sources = []

        for index, chunk in enumerate(
            chunks
        ):
            web = chunk.get("web") or {}

            if (
                web.get("uri")
                or web.get("title")
            ):
                sources.append(
                    {
                        "sourceIndex": index,
                        "title":
                            web.get("title"),
                        "uri":
                            web.get("uri"),
                    }
                )

        return LLMResponse(
            data=data,
            raw=raw,
            sources=sources,
            search_executed=bool(
                metadata.get(
                    "webSearchQueries"
                )
                or sources
            ),
        )