from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from llm.base import LLMProvider, LLMRequest, LLMResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class LLMCacheConfig:
    enabled: bool = True
    ttl_seconds: int = 86400
    grounded_ttl_seconds: int = 900
    poll_seconds: float = 0.5
    wait_timeout_seconds: float = 900.0
    claim_stale_seconds: int = 900

    def validate(self) -> None:
        if self.ttl_seconds <= 0:
            raise ValueError("LLM cache ttl_seconds must be > 0")
        if self.grounded_ttl_seconds <= 0:
            raise ValueError("LLM cache grounded_ttl_seconds must be > 0")
        if self.poll_seconds <= 0:
            raise ValueError("LLM cache poll_seconds must be > 0")
        if self.wait_timeout_seconds <= 0:
            raise ValueError("LLM cache wait_timeout_seconds must be > 0")
        if self.claim_stale_seconds <= 0:
            raise ValueError("LLM cache claim_stale_seconds must be > 0")


@dataclass(frozen=True, slots=True)
class CacheLookup:
    action: str
    response: LLMResponse | None = None


class LLMCacheStore(Protocol):
    def claim(
        self,
        *,
        cache_key: str,
        provider: str,
        model: str,
        request_payload: dict[str, Any],
        ttl_seconds: int,
        claim_stale_seconds: int,
    ) -> CacheLookup: ...

    def complete(
        self,
        *,
        cache_key: str,
        response: LLMResponse,
        ttl_seconds: int,
    ) -> None: ...

    def release(self, *, cache_key: str, error: str) -> None: ...


class PostgresLLMCacheStore:
    def __init__(self, session_factory: sessionmaker) -> None:
        self.session_factory = session_factory

    def claim(
        self,
        *,
        cache_key: str,
        provider: str,
        model: str,
        request_payload: dict[str, Any],
        ttl_seconds: int,
        claim_stale_seconds: int,
    ) -> CacheLookup:
        with self.session_factory() as db:
            row = (
                db.execute(
                    text(
                        """
                        SELECT *
                        FROM public.sp_llm_claim_cached_response(
                            CAST(:cache_key AS varchar),
                            CAST(:provider AS varchar),
                            CAST(:model AS varchar),
                            CAST(:request_payload AS jsonb),
                            CAST(:ttl_seconds AS integer),
                            CAST(:claim_stale_seconds AS integer)
                        )
                        """
                    ),
                    {
                        "cache_key": cache_key,
                        "provider": provider,
                        "model": model,
                        "request_payload": json.dumps(request_payload),
                        "ttl_seconds": ttl_seconds,
                        "claim_stale_seconds": claim_stale_seconds,
                    },
                )
                .mappings()
                .one()
            )
            db.commit()

        action = str(row["action"])
        if action != "HIT":
            return CacheLookup(action=action)

        return CacheLookup(
            action=action,
            response=LLMResponse(
                data=dict(row["response_data"] or {}),
                raw=dict(row["response_raw"] or {}),
                sources=list(row["sources"] or []),
                search_executed=bool(row["search_executed"]),
            ),
        )

    def complete(
        self,
        *,
        cache_key: str,
        response: LLMResponse,
        ttl_seconds: int,
    ) -> None:
        with self.session_factory() as db:
            db.execute(
                text(
                    """
                    SELECT public.sp_llm_complete_cached_response(
                        CAST(:cache_key AS varchar),
                        CAST(:response_data AS jsonb),
                        CAST(:response_raw AS jsonb),
                        CAST(:sources AS jsonb),
                        CAST(:search_executed AS boolean),
                        CAST(:ttl_seconds AS integer)
                    )
                    """
                ),
                {
                    "cache_key": cache_key,
                    "response_data": json.dumps(response.data),
                    "response_raw": json.dumps(response.raw),
                    "sources": json.dumps(response.sources),
                    "search_executed": response.search_executed,
                    "ttl_seconds": ttl_seconds,
                },
            ).scalar_one()
            db.commit()

    def release(self, *, cache_key: str, error: str) -> None:
        with self.session_factory() as db:
            db.execute(
                text(
                    """
                    SELECT public.sp_llm_release_cached_response(
                        CAST(:cache_key AS varchar),
                        CAST(:error AS text)
                    )
                    """
                ),
                {"cache_key": cache_key, "error": error[:4000]},
            ).scalar_one()
            db.commit()


def canonical_request_payload(
    request: LLMRequest,
    *,
    provider: str,
    model: str,
) -> dict[str, Any]:
    return {
        "provider": provider,
        "model": model,
        "prompt": request.prompt,
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
        "grounded_search": request.grounded_search,
        "response_schema": request.response_schema,
        "cache_namespace": request.cache_namespace,
        "cache_format_version": 1,
    }


def build_cache_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class CachedLLMProvider(LLMProvider):
    def __init__(
        self,
        provider: LLMProvider,
        store: LLMCacheStore,
        *,
        provider_name: str,
        model: str,
        config: LLMCacheConfig,
    ) -> None:
        config.validate()
        self.provider = provider
        self.store = store
        self.provider_name = provider_name
        self.model = model
        self.config = config
        self.max_retries = getattr(provider, "max_retries", 0)

    def generate_json(self, request: LLMRequest) -> LLMResponse:
        if not self.config.enabled or request.cache_bypass:
            return self.provider.generate_json(request)

        payload = canonical_request_payload(
            request,
            provider=self.provider_name,
            model=self.model,
        )
        cache_key = build_cache_key(payload)
        ttl_seconds = request.cache_ttl_seconds or (
            self.config.grounded_ttl_seconds
            if request.grounded_search
            else self.config.ttl_seconds
        )
        if ttl_seconds <= 0:
            raise ValueError("LLM request cache_ttl_seconds must be > 0")

        deadline = time.monotonic() + self.config.wait_timeout_seconds
        while True:
            lookup = self.store.claim(
                cache_key=cache_key,
                provider=self.provider_name,
                model=self.model,
                request_payload=payload,
                ttl_seconds=ttl_seconds,
                claim_stale_seconds=self.config.claim_stale_seconds,
            )

            if lookup.action == "HIT":
                logger.info(
                    "LLM cache hit cache_key=%s provider=%s model=%s",
                    cache_key,
                    self.provider_name,
                    self.model,
                )
                if lookup.response is None:
                    raise RuntimeError("LLM cache returned HIT without a response")
                return lookup.response

            if lookup.action == "CLAIM":
                logger.info(
                    "LLM cache miss claimed cache_key=%s provider=%s model=%s",
                    cache_key,
                    self.provider_name,
                    self.model,
                )
                try:
                    response = self.provider.generate_json(request)
                    self.store.complete(
                        cache_key=cache_key,
                        response=response,
                        ttl_seconds=ttl_seconds,
                    )
                    return response
                except Exception as error:
                    self.store.release(
                        cache_key=cache_key,
                        error=str(error),
                    )
                    raise

            if lookup.action != "WAIT":
                raise RuntimeError(f"Unknown LLM cache action: {lookup.action}")

            if time.monotonic() >= deadline:
                raise TimeoutError(f"Timed out waiting for LLM cache key {cache_key}")
            time.sleep(self.config.poll_seconds)
