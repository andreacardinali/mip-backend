from __future__ import annotations

from typing import Any

from llm.fingerprint import fingerprint


def prompt_contract_fingerprint(
    *,
    name: str,
    instructions: str,
    response_schema: dict[str, Any],
) -> str:
    return fingerprint(
        {
            "name": name,
            "instructions": instructions,
            "response_schema": response_schema,
        }
    )
