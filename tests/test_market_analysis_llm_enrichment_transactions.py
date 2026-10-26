from llm.base import LLMResponse
from services.market_analysis_llm_enrichment import (
    run_market_analysis_llm_enrichment,
)


class FakeProvider:
    def generate_json(self, request):
        marker = request.prompt.split('"signal_id": "', 1)[1].split('"', 1)[0]
        return LLMResponse(
            data={
                "signal_id": marker,
                "llm_score": 75,
                "verdict": "CONFIRM",
                "summary": "Evidence is coherent.",
                "thesis": ["Technical evidence is aligned"],
                "risks": [],
                "missing_evidence": [],
                "confidence": 0.7,
            },
            raw={},
        )


def _signal(signal_id):
    return {
        "signal_id": signal_id,
        "technical_score": 80,
        "financial_score": None,
        "catalyst_score": None,
    }


def test_persists_each_signal_immediately_and_isolates_later_failure():
    persisted = []

    def persist(signal, enrichment, analysis, composite_score):
        persisted.append(str(signal["signal_id"]))
        if signal["signal_id"] == "signal-2":
            raise RuntimeError("database unavailable")
        return True

    result = run_market_analysis_llm_enrichment(
        provider=FakeProvider(),
        candidates=[
            _signal("signal-1"),
            _signal("signal-2"),
            _signal("signal-3"),
        ],
        persist_enrichment=persist,
    )

    assert persisted == ["signal-1", "signal-2", "signal-3"]
    assert result["signal_ids"] == ["signal-1", "signal-3"]
    assert result["failed"] == 1
    assert result["status"] == "PARTIAL"
