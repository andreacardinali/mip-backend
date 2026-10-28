import logging
import random
import re
import time
from collections.abc import Callable, Mapping
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from enum import StrEnum
from typing import Any
from urllib.parse import quote, quote_plus

import httpx

logger = logging.getLogger(__name__)

HttpClient = httpx.Client
HttpRequestError = httpx.RequestError
HttpResponse = httpx.Response
HttpStatusError = httpx.HTTPStatusError


def create_http_client(**kwargs: Any) -> HttpClient:
    return httpx.Client(**kwargs)


DEFAULT_RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
NEVER_RETRY_STATUS_CODES = frozenset({403})
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
SENSITIVE_FIELDS = frozenset(
    {
        "token",
        "api_key",
        "apikey",
        "api-key",
        "access_token",
        "refresh_token",
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "password",
        "secret",
        "client_secret",
        "cookie",
        "set-cookie",
    }
)
ACTIVE_SECRETS: ContextVar[tuple[str, ...]] = ContextVar("http_secrets", default=())
SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)([\"\']?(?:api[_-]?key|(?:access_|refresh_)?token|password|"
    r"client_secret|secret|authorization|proxy-authorization|x-api-key|cookie|"
    r"set-cookie)[\"\']?\s*[:=]\s*)(?:\"[^\"]*\"|\'[^\']*\'|"
    r"(?:(?:Bearer|Basic)\s+)?[^\s&,;}]+)"
)


def _redact_text(text: str, secrets: tuple[str, ...]) -> str:
    for secret in secrets:
        text = text.replace(secret, "[REDACTED]")
    return SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", text)


def redact_text(text: str, *, sensitive_values: tuple[str, ...] = ()) -> str:
    secrets = {value for value in sensitive_values if value}
    for value in tuple(secrets):
        secrets.update({quote(value, safe=""), quote_plus(value)})
    return _redact_text(text, tuple(sorted(secrets, key=len, reverse=True)))


def _request_secrets(
    client: httpx.Client,
    url: str,
    request_kwargs: dict[str, Any],
    sensitive_values: tuple[str, ...] = (),
) -> tuple[str, ...]:
    values = {value for value in sensitive_values if value}
    query = httpx.URL(url).params.multi_items()
    headers = []
    if isinstance(client, httpx.Client):
        query += client.params.multi_items()
        headers += client.headers.multi_items()
    params = request_kwargs.get("params")
    if params is not None:
        query += httpx.QueryParams(params).multi_items()
    headers += httpx.Headers(request_kwargs.get("headers") or {}).multi_items()
    for key, value in [*query, *headers]:
        if key.casefold() in SENSITIVE_FIELDS and value:
            values.add(value)
            if key.casefold() in {"authorization", "proxy-authorization"}:
                values.add(value.split(" ", 1)[-1])
    pending = [request_kwargs.get("json"), request_kwargs.get("data")]
    while pending:
        payload = pending.pop()
        if isinstance(payload, Mapping):
            for key, value in payload.items():
                if str(key).casefold() in SENSITIVE_FIELDS and isinstance(value, str):
                    if value:
                        values.add(value)
                else:
                    pending.append(value)
        elif isinstance(payload, (list, tuple)):
            pending.extend(payload)
    for value in tuple(values):
        values.update({quote(value, safe=""), quote_plus(value)})
    return tuple(sorted(values, key=len, reverse=True))


class _SecretFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = _redact_text(record.getMessage(), ACTIVE_SECRETS.get())
        record.args = ()
        return True


logging.getLogger("httpx").addFilter(_SecretFilter())


def get_http_status(error: BaseException) -> int | None:
    """Return the HTTP status of an error or its explicit cause."""
    http_error = error if isinstance(error, httpx.HTTPStatusError) else error.__cause__
    if isinstance(http_error, httpx.HTTPStatusError):
        return http_error.response.status_code
    return None


def _raise_for_status(response: httpx.Response, secrets: tuple[str, ...]) -> None:
    try:
        response.raise_for_status()
    except httpx.HTTPStatusError as exc:
        exc.args = (_redact_text(str(exc), secrets),)
        raise


def _response_error_message(
    response: httpx.Response, secrets: tuple[str, ...] = ()
) -> str:
    try:
        body = _redact_text(response.text.strip(), secrets)
    except Exception as exc:
        return f"<unable to read response body: {type(exc).__name__}>"
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
    sensitive_values: tuple[str, ...] = (),
    sleep: Callable[[float], None] = time.sleep,
    random_uniform: Callable[[float, float], float] = random.uniform,
    **request_kwargs: Any,
) -> httpx.Response:
    policy.validate()
    normalized_method = method.strip().upper()
    if not normalized_method:
        raise ValueError("HTTP method cannot be empty")

    may_retry = normalized_method in IDEMPOTENT_METHODS or retry_non_idempotent
    secrets = _request_secrets(client, url, request_kwargs, sensitive_values)

    for attempt in range(policy.max_retries + 1):
        try:
            context_token = ACTIVE_SECRETS.set(secrets)
            try:
                response = client.request(normalized_method, url, **request_kwargs)
            finally:
                ACTIVE_SECRETS.reset(context_token)
        except httpx.RequestError as exc:
            exc.args = (_redact_text(str(exc), secrets),)
            if (
                not isinstance(exc, RETRYABLE_EXCEPTIONS)
                or not may_retry
                or attempt >= policy.max_retries
            ):
                raise
            delay = _retry_delay(
                attempt=attempt,
                policy=policy,
                response_hint=None,
                random_uniform=random_uniform,
            )
            logger.warning(
                "HTTP request retry after exception method=%s attempt=%s/%s "
                "delay=%.3f error_type=%s error=%s",
                normalized_method,
                attempt + 1,
                policy.max_retries + 1,
                delay,
                type(exc).__name__,
                str(exc) or "<empty exception message>",
            )
            sleep(delay)
            continue

        if response.status_code in NEVER_RETRY_STATUS_CODES:
            _raise_for_status(response, secrets)
            return response

        if response.status_code not in policy.retryable_status_codes:
            _raise_for_status(response, secrets)
            return response

        if not may_retry or attempt >= policy.max_retries:
            error_message = _response_error_message(response, secrets)
            logger.error(
                "HTTP request failed permanently status=%s method=%s attempt=%s/%s "
                "error=%s",
                response.status_code,
                normalized_method,
                attempt + 1,
                policy.max_retries + 1,
                error_message,
            )
            _raise_for_status(response, secrets)

        response_hint = _response_retry_delay(response, policy=policy)
        delay = _retry_delay(
            attempt=attempt,
            policy=policy,
            response_hint=response_hint,
            random_uniform=random_uniform,
        )
        error_message = _response_error_message(response, secrets)
        logger.warning(
            "HTTP request retry status=%s method=%s attempt=%s/%s delay=%.3f error=%s",
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
