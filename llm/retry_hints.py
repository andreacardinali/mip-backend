import re
from collections.abc import Mapping
from typing import Any

import httpx

_RETRY_PATTERNS = (
    re.compile(r"try again in\s+([0-9]+(?:\.[0-9]+)?)s", re.IGNORECASE),
    re.compile(r"retry after\s+([0-9]+(?:\.[0-9]+)?)\s*seconds?", re.IGNORECASE),
)


def llm_response_delay(response: httpx.Response) -> float | None:
    if response.status_code != 429:
        return None

    text = response.text
    try:
        payload = response.json()
    except ValueError:
        payload = None

    if isinstance(payload, Mapping):
        retry_after = payload.get("retry_after")
        if isinstance(retry_after, int | float):
            return max(0.0, float(retry_after))

        error = payload.get("error")
        if isinstance(error, Mapping):
            message: Any = error.get("message")
            if isinstance(message, str):
                text = message

    for pattern in _RETRY_PATTERNS:
        match = pattern.search(text)
        if match:
            return max(0.0, float(match.group(1)))

    return None


def exception_retry_delay(exc: Exception) -> float | None:
    response = getattr(exc, "response", None)
    if isinstance(response, httpx.Response):
        return llm_response_delay(response)
    cause = getattr(exc, "__cause__", None)
    response = getattr(cause, "response", None)
    if isinstance(response, httpx.Response):
        return llm_response_delay(response)
    return None
