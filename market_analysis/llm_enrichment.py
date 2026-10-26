from __future__ import annotations

import json
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from llm import LLMProvider, LLMRequest

PROMPT_VERSION = "market-analysis-enrichment-v1"

RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "signal_id": {"type": "string"},
        "llm_score": {"type": "number", "minimum": 0, "maximum": 100},
        "verdict": {"type": "string", "enum": ["CONFIRM", "REVIEW", "REJECT"]},
        "summary": {"type": "string"},
        "thesis": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": [
        "signal_id",
        "llm_score",
        "verdict",
        "summary",
        "thesis",
        "risks",
        "missing_evidence",
        "confidence",
    ],
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


def build_prompt(signal: dict[str, Any]) -> str:
    payload = {
        key: value
        for key, value in signal.items()
        if key not in {"llm_analysis", "prompt_version"}
    }
    return (
        "You are a conservative market-signal reviewer. Evaluate only the "
        "supplied evidence. Do not invent current events, prices, fundamentals "
        "or sources. Missing information must lower confidence and be listed in "
        "missing_evidence. Return exactly one JSON object matching the schema.\n\n"
        "Scoring: 0-39 reject, 40-69 review, 70-100 confirm. The verdict must "
        "match the score band. Keep summary under 500 characters and each list "
        "to at most 5 concise items. Preserve signal_id exactly.\n\n"
        "Required top-level keys: signal_id, llm_score, verdict, summary, "
        "thesis, risks, missing_evidence, confidence.\n\n"
        f"JSON_SCHEMA={json.dumps(RESPONSE_SCHEMA, separators=(',', ':'))}\n\n"
        f"SIGNAL={json.dumps(payload, default=str, sort_keys=True)}"
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

    required_keys = {
        "signal_id",
        "llm_score",
        "verdict",
        "summary",
        "thesis",
        "risks",
        "missing_evidence",
        "confidence",
    }
    missing_keys = sorted(required_keys - data.keys())
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

    expected = "REJECT" if score < 40 else "REVIEW" if score < 70 else "CONFIRM"
    normalized_verdict = expected

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
