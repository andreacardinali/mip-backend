from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def provider_config_fingerprint(
    *,
    provider: str,
    model: str,
    base_url: str,
    json_config: dict[str, Any],
    think_enabled: bool,
) -> str:
    return fingerprint(
        {
            "provider": provider,
            "model": model,
            "base_url": base_url,
            "json_config": json_config,
            "think_enabled": think_enabled,
        }
    )
