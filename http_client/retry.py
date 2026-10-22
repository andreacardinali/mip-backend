import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any

import httpx

logger = logging.getLogger(__name__)

DEFAULT_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
RETRYABLE_EXCEPTIONS = (
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.ReadTimeout,
    httpx.WriteTimeout,
    httpx.PoolTimeout,
    httpx.RemoteProtocolError,
)
IDEMPOTENT_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE", "PUT", "DELETE"})
MAX_ERROR_BODY_CHARACTERS = 2000


def _response_error_message(
    response: httpx.Response,
) -> str:
    try:
        body = response.text.strip()
    except Exception as exc:
        return "<unable to read response body: " f"{type(exc).__name__}: {exc}>"

    if not body:
        return "<empty response body>"

    body = " ".join(body.split())

    if len(body) > MAX_ERROR_BODY_CHARACTERS:
        return body[:MAX_ERROR_BODY_CHARACTERS] + "... [truncated]"

    return body


class RetryDelayStrategy(StrEnum):
    EXPONENTIAL = "exponential"
    RETRY_AFTER = "retry_after"
    RESPONSE_HINT = "response_hint"
    HYBRID = "hybrid"


ResponseDelayResolver = Callable[[httpx.Response], float | None]


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """HTTP retry policy. max_retries excludes the initial attempt."""

    max_retries: int = 0
    base_delay_seconds: float = 1.0
    max_delay_seconds: float = 120.0
    jitter_seconds: float = 0.5
    delay_strategy: RetryDelayStrategy = RetryDelayStrategy.HYBRID
    response_delay_resolver: ResponseDelayResolver | None = None
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

    may_retry = normalized_method in IDEMPOTENT_METHODS or retry_non_idempotent

    for attempt in range(policy.max_retries + 1):
        try:
            response = client.request(normalized_method, url, **request_kwargs)
        except RETRYABLE_EXCEPTIONS as exc:
            if not may_retry or attempt >= policy.max_retries:
                raise
            delay = _retry_delay(
                attempt=attempt,
                policy=policy,
                response_hint=None,
                random_uniform=random_uniform,
            )
            logger.warning(
                "HTTP request retry after exception "
                "method=%s attempt=%s/%s delay=%.3f "
                "error_type=%s error=%s",
                normalized_method,
                attempt + 1,
                policy.max_retries + 1,
                delay,
                type(exc).__name__,
                str(exc) or "<empty exception message>",
            )
            sleep(delay)
            continue

        if response.status_code not in policy.retryable_status_codes:
            response.raise_for_status()
            return response

        if not may_retry or attempt >= policy.max_retries:
            error_message = _response_error_message(response)

            logger.error(
                "HTTP request failed permanently "
                "status=%s method=%s attempt=%s/%s "
                "error=%s",
                response.status_code,
                normalized_method,
                attempt + 1,
                policy.max_retries + 1,
                error_message,
            )
            response.raise_for_status()

        response_hint = _response_retry_delay(response, policy=policy)
        delay = _retry_delay(
            attempt=attempt,
            policy=policy,
            response_hint=response_hint,
            random_uniform=random_uniform,
        )
        error_message = _response_error_message(response)
        logger.warning(
            "HTTP request retry status=%s "
            "method=%s attempt=%s/%s delay=%.3f "
            "error=%s",
            response.status_code,
            normalized_method,
            attempt + 1,
            policy.max_retries + 1,
            delay,
            error_message,
        )
        response.close()
        sleep(delay)

    raise RuntimeError("HTTP retry loop terminated unexpectedly")


def _response_retry_delay(
    response: httpx.Response, *, policy: RetryPolicy
) -> float | None:
    if policy.delay_strategy in {
        RetryDelayStrategy.RETRY_AFTER,
        RetryDelayStrategy.HYBRID,
    }:
        delay = _parse_retry_after(response.headers.get("Retry-After"))
        if delay is not None:
            return delay

    if (
        policy.delay_strategy
        in {RetryDelayStrategy.RESPONSE_HINT, RetryDelayStrategy.HYBRID}
        and policy.response_delay_resolver is not None
    ):
        return policy.response_delay_resolver(response)

    return None


def _retry_delay(
    *,
    attempt: int,
    policy: RetryPolicy,
    response_hint: float | None,
    random_uniform: Callable[[float, float], float],
) -> float:
    base_delay = (
        response_hint
        if response_hint is not None
        else policy.base_delay_seconds * (2**attempt)
    )
    bounded_delay = min(base_delay, policy.max_delay_seconds)
    jitter = (
        random_uniform(0.0, policy.jitter_seconds) if policy.jitter_seconds else 0.0
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
        retry_at = retry_at.replace(tzinfo=UTC)
    return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
