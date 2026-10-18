import random
import time
from collections.abc import Callable

import httpx


RETRYABLE_STATUS_CODES = {
    408,
    429,
    500,
    502,
    503,
    504,
}


def post_with_retry(
    client: httpx.Client,
    url: str,
    *,
    max_retries: int,
    headers: dict | None = None,
    json: dict | None = None,
) -> httpx.Response:
    for attempt in range(
        max_retries + 1
    ):
        try:
            response = client.post(
                url,
                headers=headers,
                json=json,
            )

        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.WriteTimeout,
            httpx.RemoteProtocolError,
        ):
            if attempt >= max_retries:
                raise

            _sleep_before_retry(
                attempt=attempt,
                retry_after=None,
            )
            continue

        if (
            response.status_code
            not in RETRYABLE_STATUS_CODES
        ):
            response.raise_for_status()
            return response

        if attempt >= max_retries:
            response.raise_for_status()

        _sleep_before_retry(
            attempt=attempt,
            retry_after=response.headers.get(
                "Retry-After"
            ),
        )

    raise RuntimeError(
        "LLM retry loop terminated unexpectedly"
    )


def _sleep_before_retry(
    *,
    attempt: int,
    retry_after: str | None,
) -> None:
    delay: float

    if retry_after:
        try:
            delay = float(retry_after)
        except ValueError:
            delay = 2**attempt
    else:
        delay = 2**attempt

    delay += random.uniform(
        0.0,
        0.5,
    )

    time.sleep(delay)