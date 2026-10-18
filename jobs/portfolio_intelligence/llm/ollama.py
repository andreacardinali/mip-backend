import httpx
from jobs.portfolio_intelligence.llm.base import LLMProvider, LLMRequest, LLMResponse
from jobs.portfolio_intelligence.llm.parsing import parse_json_object
from jobs.portfolio_intelligence.llm.retry import post_with_retry

class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str, model: str, timeout: float=120, max_retries: int = 4):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout = timeout
        self.max_retries = max_retries
    def generate_json(self, request: LLMRequest) -> LLMResponse:
        from jobs.portfolio_intelligence.llm.retry import post_with_retry
        with httpx.Client(
            timeout=self.timeout
        ) as client:
            response = post_with_retry(
                client,
                f"{self.base_url}/api/generate",
                max_retries=self.max_retries,
                json={
                    "model": self.model,
                    "stream": False,
                    "think": False,
                    "format": "json",
                    "prompt": request.prompt,
                    "options": {
                        "temperature":
                            request.temperature,
                        "num_predict":
                            request.max_tokens,
                    },
                },
            )

            raw = response.json()
        return LLMResponse(data=parse_json_object(raw.get("response", "")),raw=raw,sources=[],search_executed=False)
