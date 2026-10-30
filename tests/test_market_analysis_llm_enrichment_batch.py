import json

import pytest

from http_client import testing as http
from llm.base import LLMResponse
from market_analysis.enrichment_contract import enrichment_fingerprint
from market_analysis.llm_enrichment import validate_batch_enrichment
from services.market_analysis_llm_enrichment import run_market_analysis_llm_enrichment


def _analysis(signal_id: str, score: int = 75) -> dict:
    return {
        "signal_id": signal_id,
        "llm_score": score,
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


class ResilientProvider:
    def __init__(self):
        self.calls = []

    def generate_json(self, request):
        if "SIGNALS=" in request.prompt:
            payload = json.loads(request.prompt.split("SIGNALS=", 1)[1])
            ids = [item["signal_id"] for item in payload]
            self.calls.append(tuple(ids))
            if "signal-bad" in ids:
                raise RuntimeError("provider rejected batch")
            return LLMResponse(
                data={
                    "analyses": [_analysis(signal_id) for signal_id in reversed(ids)]
                },
                raw={},
            )
        payload = json.loads(request.prompt.split("SIGNAL=", 1)[1])
        signal_id = payload["signal_id"]
        self.calls.append((signal_id,))
        if signal_id == "signal-bad":
            raise RuntimeError("provider rejected signal")
        return LLMResponse(data=_analysis(signal_id), raw={})


def test_batch_validation_preserves_request_order():
    values = validate_batch_enrichment(
        {"analyses": [_analysis("signal-2"), _analysis("signal-1")]},
        ["signal-1", "signal-2"],
    )
    assert [value.signal_id for value in values] == ["signal-1", "signal-2"]


def test_batch_validation_rejects_partial_response():
    try:
        validate_batch_enrichment(
            {"analyses": [_analysis("signal-1")]},
            ["signal-1", "signal-2"],
        )
    except ValueError as error:
        assert "missing=signal-2" in str(error)
    else:
        raise AssertionError("partial batch response must be rejected")


def test_resilient_batch_split_isolates_bad_signal_and_persists_others():
    provider = ResilientProvider()
    persisted = []
    contract_fingerprint = enrichment_fingerprint()
    result = run_market_analysis_llm_enrichment(
        provider=provider,
        candidates=[_signal("signal-1"), _signal("signal-bad"), _signal("signal-3")],
        persist_enrichment=lambda signal, *_: persisted.append(signal["signal_id"])
        or True,
        enrichment_fingerprint=contract_fingerprint,
        call_batch_size=3,
    )
    assert persisted == ["signal-1", "signal-3"]
    assert result["signal_ids"] == ["signal-1", "signal-3"]
    assert result["enrichment_fingerprint"] == contract_fingerprint
    assert result["failed"] == 1
    assert result["status"] == "PARTIAL"
    assert provider.calls[0] == ("signal-1", "signal-bad", "signal-3")


def test_duplicate_candidates_are_not_sent_twice():
    provider = ResilientProvider()
    persisted = []
    contract_fingerprint = enrichment_fingerprint()
    result = run_market_analysis_llm_enrichment(
        provider=provider,
        candidates=[_signal("signal-1"), _signal("signal-1")],
        persist_enrichment=lambda signal, *_: persisted.append(signal["signal_id"])
        or True,
        enrichment_fingerprint=contract_fingerprint,
        call_batch_size=4,
    )
    assert persisted == ["signal-1"]
    assert result["enrichment_fingerprint"] == contract_fingerprint
    assert result["selected"] == 2
    assert result["unique"] == 1
    assert result["failed"] == 1


@pytest.mark.parametrize("batch_size", [1, 3])
@pytest.mark.parametrize("successful_batches", [0, 1])
@pytest.mark.parametrize(
    "failure", [401, 403, 404, 429, 503, "timeout", "connect", "url", "queue_timeout"]
)
def test_endpoint_failure_stops_remaining_calls(
    batch_size, successful_batches, failure
):
    provider = ResilientProvider()
    original_generate = provider.generate_json
    calls = []
    request = http.Request("POST", "http://localhost/ai")

    def generate(request_body):
        calls.append(request_body)
        if len(calls) <= successful_batches:
            return original_generate(request_body)
        if isinstance(failure, int):
            response = http.Response(failure, request=request)
            try:
                response.raise_for_status()
            except http.HTTPStatusError as error:
                raise RuntimeError("wrapped provider failure") from error
        if failure == "timeout":
            raise http.ReadTimeout("timeout", request=request)
        if failure == "connect":
            raise http.ConnectError("unreachable", request=request)
        if failure == "queue_timeout":
            raise TimeoutError("Timed out waiting for LLM queue request")
        raise http.InvalidURL("invalid endpoint")

    provider.generate_json = generate
    candidates = [_signal(f"signal-{index}") for index in range(9)]
    persisted = []
    result = run_market_analysis_llm_enrichment(
        provider=provider,
        candidates=candidates,
        persist_enrichment=lambda signal, *_: persisted.append(signal["signal_id"])
        or True,
        enrichment_fingerprint=enrichment_fingerprint(),
        call_batch_size=batch_size,
    )
    assert len(calls) == successful_batches + 1
    assert len(persisted) == successful_batches * batch_size
    assert result["signal_ids"] == persisted
    assert result["failed"] == len(candidates) - len(persisted)
    assert result["status"] == "PARTIAL"
    assert result["reason"] == "endpoint_unavailable"
    assert result["workload"]["split_batches"] == 0


def test_endpoint_failure_during_split_preserves_successful_sibling():
    provider = ResilientProvider()
    original_generate = provider.generate_json
    calls = []

    def generate(request):
        calls.append(request)
        if len(calls) == 1:
            raise ValueError("invalid batch response")
        if len(calls) == 2:
            return original_generate(request)
        raise http.ConnectError("endpoint unavailable")

    provider.generate_json = generate
    persisted = []
    result = run_market_analysis_llm_enrichment(
        provider=provider,
        candidates=[_signal(f"signal-{index}") for index in range(5)],
        persist_enrichment=lambda signal, *_: persisted.append(signal["signal_id"])
        or True,
        enrichment_fingerprint=enrichment_fingerprint(),
        call_batch_size=3,
    )
    assert len(calls) == 3
    assert persisted == ["signal-0"]
    assert result["signal_ids"] == persisted
    assert result["failed"] == 4
    assert result["reason"] == "endpoint_unavailable"
