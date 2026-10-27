from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker

from llm.base import LLMProvider, LLMRequest, LLMResponse
from llm.fingerprint import fingerprint

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
        for name, value in (
            ("ttl_seconds", self.ttl_seconds),
            ("grounded_ttl_seconds", self.grounded_ttl_seconds),
            ("poll_seconds", self.poll_seconds),
            ("wait_timeout_seconds", self.wait_timeout_seconds),
            ("claim_stale_seconds", self.claim_stale_seconds),
        ):
            if value <= 0:
                raise ValueError(f"LLM cache {name} must be > 0")


@dataclass(frozen=True, slots=True)
class CacheLookup:
    action: str
    response: LLMResponse | None = None


@dataclass(frozen=True, slots=True)
class LLMRequestIdentity:
    request_fingerprint: str
    prompt_fingerprint: str
    provider_config_fingerprint: str
    payload: dict[str, Any]


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
        self, *, cache_key: str, response: LLMResponse, ttl_seconds: int
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
                    SELECT * FROM public.sp_llm_claim_cached_response(
                        CAST(:cache_key AS varchar), CAST(:provider AS varchar),
                        CAST(:model AS varchar), CAST(:request_payload AS jsonb),
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
        self, *, cache_key: str, response: LLMResponse, ttl_seconds: int
    ) -> None:
        with self.session_factory() as db:
            db.execute(
                text(
                    """
                    SELECT public.sp_llm_complete_cached_response(
                        CAST(:cache_key AS varchar), CAST(:response_data AS jsonb),
                        CAST(:response_raw AS jsonb), CAST(:sources AS jsonb),
                        CAST(:search_executed AS boolean), CAST(:ttl_seconds AS integer)
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
                    "SELECT public.sp_llm_release_cached_response(CAST(:cache_key AS varchar), CAST(:error AS text))"
                ),
                {"cache_key": cache_key, "error": error[:4000]},
            ).scalar_one()
            db.commit()


def identify_request(
    request: LLMRequest,
    *,
    provider: str,
    model: str,
    provider_config_fingerprint: str,
) -> LLMRequestIdentity:
    prompt_identity = request.prompt_fingerprint or fingerprint(
        {"rendered_prompt": request.prompt, "response_schema": request.response_schema}
    )
    payload = {
        "provider": {
            "name": provider,
            "model": model,
            "configuration_fingerprint": provider_config_fingerprint,
        },
        "prompt": {
            "fingerprint": prompt_identity,
            "rendered": request.prompt,
        },
        "generation": {
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "grounded_search": request.grounded_search,
            "response_schema": request.response_schema,
        },
        "cache_format": "llm-request",
    }
    return LLMRequestIdentity(
        request_fingerprint=fingerprint(payload),
        prompt_fingerprint=prompt_identity,
        provider_config_fingerprint=provider_config_fingerprint,
        payload=payload,
    )


class CachedLLMProvider(LLMProvider):
    def __init__(
        self,
        provider: LLMProvider,
        store: LLMCacheStore,
        *,
        provider_name: str,
        model: str,
        provider_config_fingerprint: str,
        config: LLMCacheConfig,
    ) -> None:
        config.validate()
        self.provider = provider
        self.store = store
        self.provider_name = provider_name
        self.model = model
        self.provider_config_fingerprint = provider_config_fingerprint
        self.config = config
        self.max_retries = getattr(provider, "max_retries", 0)

    def generate_json(self, request: LLMRequest) -> LLMResponse:
        if not self.config.enabled or request.cache_bypass:
            return self.provider.generate_json(request)
        identity = identify_request(
            request,
            provider=self.provider_name,
            model=self.model,
            provider_config_fingerprint=self.provider_config_fingerprint,
        )
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
                cache_key=identity.request_fingerprint,
                provider=self.provider_name,
                model=self.model,
                request_payload=identity.payload,
                ttl_seconds=ttl_seconds,
                claim_stale_seconds=self.config.claim_stale_seconds,
            )
            if lookup.action == "HIT":
                if lookup.response is None:
                    raise RuntimeError("LLM cache returned HIT without a response")
                return lookup.response
            if lookup.action == "CLAIM":
                try:
                    response = self.provider.generate_json(request)
                    self.store.complete(
                        cache_key=identity.request_fingerprint,
                        response=response,
                        ttl_seconds=ttl_seconds,
                    )
                    return response
                except Exception as error:
                    self.store.release(
                        cache_key=identity.request_fingerprint, error=str(error)
                    )
                    raise
            if lookup.action != "WAIT":
                raise RuntimeError(f"Unknown LLM cache action: {lookup.action}")
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"Timed out waiting for LLM cache key {identity.request_fingerprint}"
                )
            time.sleep(self.config.poll_seconds)
