import logging

from llm.base import LLMResponse
from services.market_analysis_llm_enrichment import _enrich_resilient


class Progress:
    llm_calls = 0
    split_batches = 0

    def record_llm_call(self):
        self.llm_calls += 1

    def record_split(self):
        self.split_batches += 1


class Provider:
    def generate_json(self, request):
        return LLMResponse(
            data={
                "signal_id": "signal-1",
                "llm_score": 75,
                "verdict": "CONFIRM",
                "summary": "Evidence is coherent.",
                "thesis": [],
                "risks": [],
                "missing_evidence": [],
                "confidence": 0.7,
            },
            raw={},
        )


def test_logs_provider_request_start_and_completion(caplog):
    with caplog.at_level(
        logging.INFO,
        logger="services.market_analysis_llm_enrichment",
    ):
        successful, failures, stopped = _enrich_resilient(
            provider=Provider(),
            signals=[{"signal_id": "signal-1"}],
            max_tokens=500,
            progress=Progress(),
        )

    assert len(successful) == 1
    assert failures == []
    assert stopped is False
    assert "LLM request started" in caplog.text
    assert "call=1" in caplog.text
    assert "mode=single" in caplog.text
    assert "signal_count=1" in caplog.text
    assert "max_output_tokens=500" in caplog.text
    assert "LLM request completed" in caplog.text
    assert "elapsed_seconds=" in caplog.text
