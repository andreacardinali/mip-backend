from llm.base import LLMResponse
from market_analysis.enrichment_contract import enrichment_fingerprint
from services.market_analysis_llm_enrichment import run_market_analysis_llm_enrichment


def _analysis(signal_id: str) -> dict:
    return {
        "signal_id": signal_id,
        "llm_score": 75,
        "verdict": "CONFIRM",
        "summary": "Evidence is coherent.",
        "thesis": ["Technical evidence is aligned"],
        "risks": [],
        "missing_evidence": [],
        "confidence": 0.7,
    }


def _signal(signal_id: str) -> dict:
    return {
        "signal_id": signal_id,
        "technical_score": 80,
        "financial_score": None,
        "catalyst_score": None,
    }


class FakeProvider:
    def generate_json(self, request):
        signal_id = request.prompt.split('"signal_id": "', 1)[1].split('"', 1)[0]
        return LLMResponse(data=_analysis(signal_id), raw={})


def test_persists_each_signal_immediately_and_isolates_later_failure():
    persisted = []

    def persist(signal, enrichment, analysis, composite_score):
        persisted.append(str(signal["signal_id"]))
        if signal["signal_id"] == "signal-2":
            raise RuntimeError("database unavailable")
        return True

    contract_fingerprint = enrichment_fingerprint()
    result = run_market_analysis_llm_enrichment(
        provider=FakeProvider(),
        candidates=[
            _signal("signal-1"),
            _signal("signal-2"),
            _signal("signal-3"),
        ],
        persist_enrichment=persist,
        enrichment_fingerprint=contract_fingerprint,
    )

    assert persisted == ["signal-1", "signal-2", "signal-3"]
    assert result["signal_ids"] == ["signal-1", "signal-3"]
    assert result["enrichment_fingerprint"] == contract_fingerprint
    assert result["failed"] == 1
    assert result["status"] == "PARTIAL"
