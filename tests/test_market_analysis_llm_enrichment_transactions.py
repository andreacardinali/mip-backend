import pytest

from llm.base import LLMResponse
from market_analysis.enrichment_contract import enrichment_fingerprint
from services import market_analysis_llm_enrichment as service
from services.market_analysis_llm_enrichment import run_market_analysis_llm_enrichment


def test_configured_enrichment_skips_provider_when_context_has_no_candidates(
    monkeypatch,
):
    def unexpected_provider(config):
        raise AssertionError("No provider should be created for an empty context")

    monkeypatch.setattr(service, "create_provider", unexpected_provider)
    calls = []
    result = service.run_configured_market_analysis_llm_enrichment(
        settings={
            "MARKET_ANALYSIS_ENRICHMENT_RUN_MAX_SIGNALS": 7,
            "MARKET_ANALYSIS_ENRICHMENT_LLM_MODEL": "test-model",
            "MARKET_ANALYSIS_ENRICHMENT_LLM_BASE_URL": "http://localhost:11434",
        },
        load_candidates=lambda **kwargs: calls.append(kwargs) or [],
        persist_enrichment=lambda *args, **kwargs: False,
    )
    assert result["status"] == "SUCCESS"
    assert result["selected"] == result["enriched"] == result["failed"] == 0
    assert calls == [
        {"contract_fingerprint": result["enrichment_fingerprint"], "limit": 7}
    ]


def test_configured_enrichment_reuses_fingerprint_and_persistence(monkeypatch):
    monkeypatch.setattr(service, "create_provider", lambda config: FakeProvider())
    selected = []
    persisted = []

    def load(**kwargs):
        selected.append(kwargs)
        return [_signal("signal-1")]

    def persist(signal, enrichment, analysis, composite, *, contract_fingerprint):
        persisted.append(
            (
                signal["signal_id"],
                analysis["enrichment_fingerprint"],
                contract_fingerprint,
            )
        )
        return True

    result = service.run_configured_market_analysis_llm_enrichment(
        settings={
            "MARKET_ANALYSIS_ENRICHMENT_LLM_MODEL": "test-model",
            "MARKET_ANALYSIS_ENRICHMENT_LLM_BASE_URL": "http://localhost:11434",
        },
        load_candidates=load,
        persist_enrichment=persist,
    )
    assert result["status"] == "SUCCESS"
    assert result["signal_ids"] == ["signal-1"]
    assert selected[0]["limit"] == 25
    assert persisted == [
        (
            "signal-1",
            selected[0]["contract_fingerprint"],
            result["enrichment_fingerprint"],
        )
    ]


@pytest.mark.parametrize(
    "overrides",
    [
        {"MODEL": ""},
        {"BASE_URL": ""},
        {"PROVIDER": "unsupported"},
        {"REQUEST_MAX_SIGNALS": 0},
        {"REQUEST_MAX_OUTPUT_TOKENS": "invalid"},
        {"JSON_CONFIG": "not-json"},
    ],
)
def test_invalid_configuration_does_not_load_or_call_provider(monkeypatch, overrides):
    settings = {
        "MARKET_ANALYSIS_ENRICHMENT_LLM_MODEL": "test-model",
        "MARKET_ANALYSIS_ENRICHMENT_LLM_BASE_URL": "http://localhost:11434",
        **{
            f"MARKET_ANALYSIS_ENRICHMENT_LLM_{key}": value
            for key, value in overrides.items()
        },
    }

    def unexpected(*args, **kwargs):
        raise AssertionError(
            "Invalid configuration must not access candidates or provider"
        )

    monkeypatch.setattr(service, "create_provider", unexpected)
    result = service.run_configured_market_analysis_llm_enrichment(
        settings=settings,
        load_candidates=unexpected,
        persist_enrichment=unexpected,
    )
    assert result == {"status": "FAILED", "reason": "invalid_configuration"}


def test_invalid_provider_configuration_returns_fallback(monkeypatch):
    def invalid_provider(config):
        raise ValueError("invalid provider configuration")

    monkeypatch.setattr(service, "create_provider", invalid_provider)
    result = service.run_configured_market_analysis_llm_enrichment(
        settings={
            "MARKET_ANALYSIS_ENRICHMENT_LLM_MODEL": "test-model",
            "MARKET_ANALYSIS_ENRICHMENT_LLM_BASE_URL": "http://localhost:11434",
        },
        load_candidates=lambda **kwargs: [_signal("signal-1")],
        persist_enrichment=lambda *args, **kwargs: False,
    )
    assert result == {"status": "FAILED", "reason": "invalid_configuration"}


def test_candidate_loading_error_is_not_invalid_configuration():
    def failed_load(**kwargs):
        raise ValueError("candidate loading failed")

    with pytest.raises(ValueError, match="candidate loading failed"):
        service.run_configured_market_analysis_llm_enrichment(
            settings={
                "MARKET_ANALYSIS_ENRICHMENT_LLM_MODEL": "test-model",
                "MARKET_ANALYSIS_ENRICHMENT_LLM_BASE_URL": "http://localhost:11434",
            },
            load_candidates=failed_load,
            persist_enrichment=lambda *args, **kwargs: False,
        )


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
