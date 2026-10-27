from __future__ import annotations

from llm.fingerprint import fingerprint
from market_analysis.llm_enrichment import (
    BATCH_RESPONSE_SCHEMA,
    RESPONSE_SCHEMA,
    _instructions,
)


def enrichment_fingerprint() -> str:
    return fingerprint(
        {
            "single": {
                "instructions": _instructions(batch=False),
                "response_schema": RESPONSE_SCHEMA,
            },
            "batch": {
                "instructions": _instructions(batch=True),
                "response_schema": BATCH_RESPONSE_SCHEMA,
            },
            "composite_score": {
                "technical": "0.70",
                "financial": "0.10",
                "catalyst": "0.10",
                "llm": "0.10",
            },
        }
    )
