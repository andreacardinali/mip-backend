import httpx
from jobs.portfolio_intelligence.llm.base import LLMProvider, LLMRequest, LLMResponse
from jobs.portfolio_intelligence.llm.parsing import parse_json_object
class OllamaProvider(LLMProvider):
    def __init__(self, base_url: str, model: str, timeout: float=120):
        self.base_url=base_url; self.model=model; self.timeout=timeout
    def generate_json(self, request: LLMRequest) -> LLMResponse:
        with httpx.Client(timeout=self.timeout) as client:
            response=client.post(f"{self.base_url}/api/generate", json={"model":self.model,"stream":False,"think":False,"format":"json","prompt":request.prompt,"options":{"temperature":request.temperature,"num_predict":request.max_tokens}})
            response.raise_for_status(); raw=response.json()
        return LLMResponse(data=parse_json_object(raw.get("response", "")),raw=raw,sources=[],search_executed=False)
