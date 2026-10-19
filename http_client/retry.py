import logging
import random
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Callable

import httpx


logger = logging.getLogger(__name__)

DEFAULT_RETRYABLE_STATUS_CODES = frozenset(
    {408, 429, 500, 502, 503, 504}
)
RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)
IDEMPOTENT_METHODS = frozenset(
    {"GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE"}
)


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """HTTP retry policy.

    max_retries is the number of retries after the initial attempt.
    Therefore max_retries=4 allows at most five HTTP attempts.
    """

    max_retries: int = 0
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 30.0
    jitter_seconds: float = 0.5
    retryable_status_codes: frozenset[int] = field(
        default_factory=lambda: DEFAULT_RETRYABLE_STATUS_CODES
    )

    def validate(self) -> None:
        if self.max_retries < 0:
            raise ValueError("max_retries must be >= 0")
        if self.base_delay_seconds < 0:
            raise ValueError("base_delay_seconds must be >= 0")
        if self.max_delay_seconds < 0:
            raise ValueError("max_delay_seconds must be >= 0")
        if self.jitter_seconds < 0:
            raise ValueError("jitter_seconds must be >= 0")


def request_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    *,
    policy: RetryPolicy,
    retry_non_idempotent: bool = False,
    sleep: Callable[[float], None] = time.sleep,
    random_uniform: Callable[[float, float], float] = random.uniform,
    **request_kwargs: Any,
) -> httpx.Response:
    policy.validate()
    normalized_method = method.strip().upper()
    if not normalized_method:
        raise ValueError("HTTP method cannot be empty")

    may_retry = (
        normalized_method in IDEMPOTENT_METHODS
        or retry_non_idempotent
    )

    for attempt in range(policy.max_retries + 1):
        try:
            response = client.request(
                normalized_method,
                url,
                **request_kwargs,
            )
        except RETRYABLE_EXCEPTIONS:
            if not may_retry or attempt >= policy.max_retries:
                raise
            delay = _retry_delay(
                attempt=attempt,
                policy=policy,
                retry_after=None,
                random_uniform=random_uniform,
            )
            logger.warning(
                "HTTP request retry after exception method=%s attempt=%s delay=%.3f",
                normalized_method,
                attempt + 1,
                delay,
            )
            sleep(delay)
            continue

        if response.status_code not in policy.retryable_status_codes:
            response.raise_for_status()
            return response

        if not may_retry or attempt >= policy.max_retries:
            response.raise_for_status()

        delay = _retry_delay(
            attempt=attempt,
            policy=policy,
            retry_after=response.headers.get("Retry-After"),
            random_uniform=random_uniform,
        )
        logger.warning(
            "HTTP request retry status=%s method=%s attempt=%s delay=%.3f",
            response.status_code,
            normalized_method,
            attempt + 1,
            delay,
        )
        response.close()
        sleep(delay)

    raise RuntimeError("HTTP retry loop terminated unexpectedly")


def _retry_delay(
    *,
    attempt: int,
    policy: RetryPolicy,
    retry_after: str | None,
    random_uniform: Callable[[float, float], float],
) -> float:
    parsed_retry_after = _parse_retry_after(retry_after)
    if parsed_retry_after is not None:
        base_delay = parsed_retry_after
    else:
        base_delay = policy.base_delay_seconds * (2 ** attempt)

    bounded_delay = min(base_delay, policy.max_delay_seconds)
    jitter = (
        random_uniform(0.0, policy.jitter_seconds)
        if policy.jitter_seconds
        else 0.0
    )
    return max(0.0, bounded_delay + jitter)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass

    try:
        retry_at = parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if retry_at.tzinfo is None:
        retry_at = retry_at.replace(tzinfo=timezone.utc)
    return max(
        0.0,
        (retry_at - datetime.now(timezone.utc)).total_seconds(),
    )
