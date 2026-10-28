from llm.base import LLMResponse
from llm.config import LLMConfig
from market_analysis.enrichment_contract import enrichment_fingerprint
from market_analysis.llm_enrichment import (
    calculate_composite_score,
    enrich_signal,
    validate_enrichment,
)


class FakeProvider:
    def generate_json(self, request):
        return LLMResponse(
            data={
                "signal_id": "signal-1",
                "llm_score": 78,
                "verdict": "CONFIRM",
                "summary": "Evidence is coherent, with explicit residual risks.",
                "thesis": ["Technical evidence is aligned"],
                "risks": ["No independent fundamental confirmation"],
                "missing_evidence": ["Recent catalyst evidence"],
                "confidence": 0.72,
            },
            raw={},
        )


def test_enrich_signal_validates_signal_id_and_payload():
    result = enrich_signal(FakeProvider(), {"signal_id": "signal-1"})
    assert result.verdict == "CONFIRM"
    assert result.llm_score == 78


def test_verdict_is_normalized_from_score_band() -> None:
    data = {
        "signal_id": "signal-1",
        "llm_score": 30,
        "verdict": "CONFIRM",
        "summary": "x",
        "thesis": [],
        "risks": [],
        "missing_evidence": [],
        "confidence": 0.5,
    }

    result = validate_enrichment(
        data,
        "signal-1",
    )

    assert result.llm_score == 30
    assert result.verdict == "REJECT"
    assert result.model_verdict == "CONFIRM"


def test_invalid_model_verdict_is_rejected() -> None:
    data = {
        "signal_id": "signal-1",
        "llm_score": 30,
        "verdict": "BUY_NOW",
        "summary": "x",
        "thesis": [],
        "risks": [],
        "missing_evidence": [],
        "confidence": 0.5,
    }

    try:
        validate_enrichment(
            data,
            "signal-1",
        )
    except ValueError as error:
        assert "CONFIRM, REVIEW or REJECT" in str(error)
    else:
        raise AssertionError("validation must reject an unsupported verdict")


def test_composite_score_renormalizes_missing_components():
    score = calculate_composite_score(
        {"technical_score": 80, "financial_score": None, "catalyst_score": None},
        60,
    )
    assert score == 77.5


def test_enrichment_identity_changes_with_model_not_credentials():
    first = LLMConfig(provider="ollama", model="model-a", base_url="http://localhost:11434")
    second = LLMConfig(provider="ollama", model="model-b", base_url=first.base_url)
    rotated = LLMConfig(provider="ollama", model=first.model, base_url=first.base_url, api_key="test-credential")
    assert enrichment_fingerprint(config=first) != enrichment_fingerprint(config=second)
    assert enrichment_fingerprint(config=first) == enrichment_fingerprint(config=rotated)
    assert enrichment_fingerprint(config=first, max_tokens=500) != enrichment_fingerprint(config=first)
