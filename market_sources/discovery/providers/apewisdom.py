from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from http_client import (
    HttpClient,
    HttpRequestError,
    HttpStatusError,
    RetryPolicy,
    create_http_client,
    request_with_retry,
)
from market_sources.capabilities import (
    MarketSourceCapability,
    SourceProviderCapabilities,
)
from market_sources.exceptions import (
    InvalidSourceResponseError,
    SourceRateLimitError,
    SourceTemporaryError,
)
from market_sources.models import (
    DiscoveryCandidate,
    DiscoveryRequest,
    DiscoverySnapshot,
    DiscoverySourceType,
)

APEWISDOM_ALL_STOCKS_URL = "https://apewisdom.io/api/v1.0/filter/all-stocks"


class ApeWisdomDiscoveryProvider:
    name = "apewisdom"
    capabilities = SourceProviderCapabilities(
        supported=frozenset({MarketSourceCapability.DISCOVERY})
    )

    def __init__(
        self,
        *,
        user_agent: str,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        client: HttpClient | None = None,
    ) -> None:
        clean_user_agent = str(user_agent or "").strip()
        if not clean_user_agent:
            raise ValueError("APEWISDOM_USER_AGENT cannot be empty")
        if timeout <= 0:
            raise ValueError("APEWISDOM_HTTP_TIMEOUT must be > 0")
        self.user_agent = clean_user_agent
        self.timeout = float(timeout)
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client

    def discover(self, request: DiscoveryRequest) -> DiscoverySnapshot:
        if request.source_type != DiscoverySourceType.REDDIT_TRENDING:
            raise InvalidSourceResponseError(
                f"ApeWisdom does not support discovery source "
                f"{request.source_type.value}"
            )

        client = self._client or create_http_client(timeout=self.timeout)
        close_client = self._client is None
        try:
            response = request_with_retry(
                client,
                "GET",
                APEWISDOM_ALL_STOCKS_URL,
                policy=self.retry_policy,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "application/json",
                },
            )
            payload = response.json()
            candidates = parse_apewisdom_candidates(payload, limit=request.limit)
            return DiscoverySnapshot(
                source_type=request.source_type,
                provider_name=self.name,
                observed_at=datetime.now(UTC),
                requested_limit=request.limit,
                candidates=tuple(candidates),
                source_reference=APEWISDOM_ALL_STOCKS_URL,
                source_metadata={
                    "returned_count": len(candidates),
                },
            )
        except HttpStatusError as error:
            status_code = error.response.status_code
            if status_code == 429:
                raise SourceRateLimitError("ApeWisdom rate limit exceeded") from error
            raise SourceTemporaryError(
                f"ApeWisdom HTTP failure: {status_code}"
            ) from error
        except HttpRequestError as error:
            raise SourceTemporaryError(f"ApeWisdom request failed: {error}") from error
        except ValueError as error:
            raise InvalidSourceResponseError(
                f"ApeWisdom returned invalid JSON: {error}"
            ) from error
        finally:
            if close_client:
                client.close()


def parse_apewisdom_candidates(
    payload: Any,
    *,
    limit: int,
) -> list[DiscoveryCandidate]:
    if not isinstance(payload, dict):
        raise InvalidSourceResponseError("ApeWisdom payload must be an object")
    results = payload.get("results")
    if not isinstance(results, list):
        raise InvalidSourceResponseError("ApeWisdom payload requires a results array")

    candidates: list[DiscoveryCandidate] = []
    seen: set[str] = set()
    for source_rank, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        symbol = normalize_apewisdom_symbol(item.get("ticker"))
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        rank = _optional_positive_int(item.get("rank"))

        candidates.append(
            DiscoveryCandidate(
                symbol=symbol,
                name=_optional_text(item.get("name")),
                rank=rank or source_rank,
                score=_optional_float(item.get("sentiment")),
                previous_rank=_optional_positive_int(
                    item.get(
                        "rank_24h_ago",
                        item.get("previous_rank"),
                    )
                ),
                mentions=_optional_non_negative_int(item.get("mentions")),
                previous_mentions=_optional_non_negative_int(
                    item.get("mentions_24h_ago")
                ),
                upvotes=_optional_non_negative_int(item.get("upvotes")),
                source_payload=dict(item),
            )
        )
        if len(candidates) >= limit:
            break
    return candidates


def normalize_apewisdom_symbol(value: Any) -> str | None:
    symbol = str(value or "").strip().upper()
    if not symbol or not symbol.isalpha():
        return None
    return symbol


def _optional_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_positive_int(
    value: object,
) -> int | None:
    if value is None:
        return None

    normalized = str(value).strip()

    if not normalized:
        return None

    try:
        parsed = int(normalized)
    except (TypeError, ValueError):
        return None

    return parsed if parsed > 0 else None


def _optional_non_negative_int(
    value: object,
) -> int | None:
    if value is None:
        return None

    normalized = str(value).strip()

    if not normalized:
        return None

    try:
        parsed = int(normalized)
    except (TypeError, ValueError):
        return None

    return parsed if parsed >= 0 else None


def _optional_text(
    value: object,
) -> str | None:
    if value is None:
        return None

    normalized = str(value).strip()

    return normalized or None
