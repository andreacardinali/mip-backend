from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from llm import LLMProvider, LLMRequest

ENRICHMENT_PROPERTIES: dict[str, Any] = {
    "signal_id": {"type": "string"},
    "llm_score": {"type": "number", "minimum": 0, "maximum": 100},
    "verdict": {"type": "string", "enum": ["CONFIRM", "REVIEW", "REJECT"]},
    "summary": {"type": "string"},
    "thesis": {"type": "array", "items": {"type": "string"}},
    "risks": {"type": "array", "items": {"type": "string"}},
    "missing_evidence": {"type": "array", "items": {"type": "string"}},
    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
}
REQUIRED_KEYS = tuple(ENRICHMENT_PROPERTIES)

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": ENRICHMENT_PROPERTIES,
    "required": list(REQUIRED_KEYS),
    "additionalProperties": False,
}

BATCH_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "analyses": {
            "type": "array",
            "items": RESPONSE_SCHEMA,
        }
    },
    "required": ["analyses"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class SignalEnrichment:
    signal_id: str
    llm_score: float
    verdict: str
    model_verdict: str
    summary: str
    thesis: list[str]
    risks: list[str]
    missing_evidence: list[str]
    confidence: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_id": self.signal_id,
            "llm_score": self.llm_score,
            "verdict": self.verdict,
            "model_verdict": self.model_verdict,
            "summary": self.summary,
            "thesis": self.thesis,
            "risks": self.risks,
            "missing_evidence": self.missing_evidence,
            "confidence": self.confidence,
        }


def _clean_signal(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in signal.items()
        if key not in {"llm_analysis", "enrichment_fingerprint"}
    }


def _instructions(*, batch: bool) -> str:
    cardinality = (
        "Return exactly one analysis for every supplied signal. Preserve every "
        "signal_id exactly. Do not omit, duplicate or add signals. "
        if batch
        else "Preserve signal_id exactly. "
    )
    return (
        "You are a conservative market-signal reviewer. Evaluate only the "
        "supplied evidence. Do not invent current events, prices, fundamentals "
        "or sources. Missing information must lower confidence and be listed in "
        "missing_evidence. "
        + cardinality
        + "Scoring: 0-39 reject, 40-69 review, 70-100 confirm. The verdict must "
        "match the score band. Keep each summary under 500 characters and each "
        "list to at most 5 concise items. Return only JSON matching the schema."
    )


def build_prompt(signal: dict[str, Any]) -> str:
    return (
        f"{_instructions(batch=False)}\n\n"
        f"JSON_SCHEMA={json.dumps(RESPONSE_SCHEMA, separators=(',', ':'))}\n\n"
        f"SIGNAL={json.dumps(_clean_signal(signal), default=str, sort_keys=True)}"
    )


def build_batch_prompt(signals: Sequence[dict[str, Any]]) -> str:
    payload = [_clean_signal(signal) for signal in signals]
    return (
        f"{_instructions(batch=True)}\n\n"
        f"JSON_SCHEMA={json.dumps(BATCH_RESPONSE_SCHEMA, separators=(',', ':'))}\n\n"
        f"SIGNALS={json.dumps(payload, default=str, sort_keys=True)}"
    )


def _string_list(data: dict[str, Any], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{key} must be a list of strings")
    return [item.strip() for item in value if item.strip()][:5]


def validate_enrichment(
    data: dict[str, Any], expected_signal_id: str
) -> SignalEnrichment:
    signal_id = str(data.get("signal_id", "")).strip()
    if signal_id != expected_signal_id:
        raise ValueError("LLM response signal_id does not match request")

    missing_keys = sorted(set(REQUIRED_KEYS) - data.keys())
    if missing_keys:
        raise ValueError(
            "LLM response missing required keys: " + ", ".join(missing_keys)
        )

    score = float(data["llm_score"])
    confidence = float(data["confidence"])
    verdict = str(data.get("verdict", "")).strip().upper()
    summary = str(data.get("summary", "")).strip()
    if not 0 <= score <= 100:
        raise ValueError("llm_score must be between 0 and 100")
    if not 0 <= confidence <= 1:
        raise ValueError("confidence must be between 0 and 1")
    if verdict not in {"CONFIRM", "REVIEW", "REJECT"}:
        raise ValueError("verdict must be CONFIRM, REVIEW or REJECT")

    normalized_verdict = (
        "REJECT" if score < 40 else "REVIEW" if score < 70 else "CONFIRM"
    )
    if not summary or len(summary) > 500:
        raise ValueError("summary must contain 1 to 500 characters")

    return SignalEnrichment(
        signal_id=signal_id,
        llm_score=round(score, 3),
        verdict=normalized_verdict,
        model_verdict=verdict,
        summary=summary,
        thesis=_string_list(data, "thesis"),
        risks=_string_list(data, "risks"),
        missing_evidence=_string_list(data, "missing_evidence"),
        confidence=round(confidence, 6),
    )


def validate_batch_enrichment(
    data: dict[str, Any], expected_signal_ids: Sequence[str]
) -> list[SignalEnrichment]:
    analyses = data.get("analyses")
    if not isinstance(analyses, list):
        raise ValueError("LLM batch response must contain an analyses list")

    expected = list(expected_signal_ids)
    if len(expected) != len(set(expected)):
        raise ValueError("batch request contains duplicate signal_id values")

    raw_by_id: dict[str, dict[str, Any]] = {}
    for item in analyses:
        if not isinstance(item, dict):
            raise ValueError("each batch analysis must be an object")
        signal_id = str(item.get("signal_id", "")).strip()
        if not signal_id:
            raise ValueError("batch analysis is missing signal_id")
        if signal_id in raw_by_id:
            raise ValueError(f"duplicate signal_id in batch response: {signal_id}")
        raw_by_id[signal_id] = item

    expected_set = set(expected)
    returned_set = set(raw_by_id)
    missing = [signal_id for signal_id in expected if signal_id not in returned_set]
    unexpected = sorted(returned_set - expected_set)
    if missing or unexpected:
        details = []
        if missing:
            details.append("missing=" + ",".join(missing))
        if unexpected:
            details.append("unexpected=" + ",".join(unexpected))
        raise ValueError(
            "LLM batch response cardinality mismatch: " + "; ".join(details)
        )

    return [
        validate_enrichment(raw_by_id[signal_id], signal_id) for signal_id in expected
    ]


def enrich_signal(
    provider: LLMProvider,
    signal: dict[str, Any],
    *,
    max_tokens: int = 1200,
) -> SignalEnrichment:
    signal_id = str(signal["signal_id"])
    response = provider.generate_json(
        LLMRequest(
            prompt=build_prompt(signal),
            temperature=0.0,
            max_tokens=max_tokens,
            grounded_search=False,
            response_schema=RESPONSE_SCHEMA,
        )
    )
    return validate_enrichment(response.data, signal_id)


def enrich_signals_batch(
    provider: LLMProvider,
    signals: Sequence[dict[str, Any]],
    *,
    max_tokens: int = 1200,
) -> list[SignalEnrichment]:
    if not signals:
        return []
    signal_ids = [str(signal["signal_id"]) for signal in signals]
    response = provider.generate_json(
        LLMRequest(
            prompt=build_batch_prompt(signals),
            temperature=0.0,
            max_tokens=max_tokens * len(signals),
            grounded_search=False,
            response_schema=BATCH_RESPONSE_SCHEMA,
        )
    )
    return validate_batch_enrichment(response.data, signal_ids)


def calculate_composite_score(signal: dict[str, Any], llm_score: float) -> float:
    weighted: list[tuple[float, Decimal]] = []
    for key, weight in (
        ("technical_score", Decimal("0.70")),
        ("financial_score", Decimal("0.10")),
        ("catalyst_score", Decimal("0.10")),
    ):
        value = signal.get(key)
        if value is not None:
            weighted.append((float(value), weight))
    weighted.append((llm_score, Decimal("0.10")))
    total_weight = sum((weight for _, weight in weighted), Decimal("0"))
    score = (
        sum(Decimal(str(value)) * weight for value, weight in weighted) / total_weight
    )
    return round(float(max(Decimal("0"), min(Decimal("100"), score))), 3)
