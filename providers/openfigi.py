import logging
import re
from difflib import SequenceMatcher
from typing import Any

import httpx

from config.settings import settings
from http_client.retry import RetryPolicy, request_with_retry

logger = logging.getLogger(__name__)


class OpenFigiProvider:
    def __init__(self) -> None:
        self.base_url = str(
            settings.get("OPENFIGI_BASE_URL", "https://api.openfigi.com/v3")
        ).rstrip("/")
        self.api_key = str(settings.get("OPENFIGI_API_KEY", "") or "").strip()
        self.timeout = float(settings.get("OPENFIGI_HTTP_TIMEOUT", 15) or 15)
        self.retry_policy = RetryPolicy(
            max_retries=int(settings.get("OPENFIGI_HTTP_MAX_RETRIES", 2) or 2),
            base_delay_seconds=float(settings.get("OPENFIGI_HTTP_RETRY_DELAY", 1) or 1),
        )

    def enrich_results(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        pending = [row for row in rows if self._needs_enrichment(row)]
        if not pending:
            return rows
        responses = self._map([self._mapping_job(row) for row in pending])
        for row, response in zip(pending, responses, strict=False):
            match = self._select_match(row, response.get("data") or [])
            if match:
                self._merge(row, match)
        return rows

    def enrich_profile(self, profile: dict[str, Any]) -> dict[str, Any]:
        if not self._needs_enrichment(profile):
            return profile
        figi = self._clean(profile.get("figi"))
        jobs = (
            [{"idType": "ID_BB_GLOBAL", "idValue": figi}]
            if figi
            else [self._mapping_job(profile)]
        )
        responses = self._map(jobs)
        if responses:
            match = self._select_match(profile, responses[0].get("data") or [])
            if match:
                self._merge(profile, match)
        return profile

    def _map(self, jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key
        with httpx.Client(timeout=self.timeout) as client:
            response = request_with_retry(
                client,
                "POST",
                f"{self.base_url}/mapping",
                policy=self.retry_policy,
                retry_non_idempotent=True,
                headers=headers,
                json=jobs,
            )
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError("Invalid OpenFIGI mapping response")
        return [item if isinstance(item, dict) else {} for item in payload]

    @classmethod
    def _mapping_job(cls, row: dict[str, Any]) -> dict[str, Any]:
        job = {"idType": "TICKER", "idValue": cls._base_symbol(row.get("symbol"))}
        currency = cls._clean(row.get("currency"))
        if currency and len(currency) == 3:
            job["currency"] = currency.upper()
        return job

    @classmethod
    def _select_match(
        cls, source: dict[str, Any], candidates: list[dict[str, Any]]
    ) -> dict[str, Any] | None:
        if not candidates:
            return None
        base_symbol = cls._base_symbol(source.get("symbol"))
        source_name = cls._normalize_text(source.get("name"))
        source_currency = cls._clean(source.get("currency"))
        source_figi = cls._clean(source.get("figi"))
        scored = []
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            score = 0.0
            if source_figi and candidate.get("figi") == source_figi:
                score += 100
            if str(candidate.get("ticker") or "").upper() == base_symbol:
                score += 30
            candidate_name = cls._normalize_text(candidate.get("name"))
            if source_name and candidate_name:
                score += 30 * SequenceMatcher(None, source_name, candidate_name).ratio()
            if (
                source_currency
                and str(candidate.get("currency") or "").upper()
                == source_currency.upper()
            ):
                score += 15
            scored.append((score, candidate))
        if not scored:
            return None
        scored.sort(key=lambda item: item[0], reverse=True)
        best_score, best = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else -1
        if not source_figi and (best_score < 45 or best_score - second_score < 5):
            return None
        return best

    @classmethod
    def _merge(cls, target: dict[str, Any], match: dict[str, Any]) -> None:
        exch_code = cls._clean(match.get("exchCode"))
        target["figi"] = target.get("figi") or match.get("figi")
        target["figi_composite"] = target.get("figi_composite") or match.get(
            "compositeFIGI"
        )
        target["figi_share_class"] = target.get("figi_share_class") or match.get(
            "shareClassFIGI"
        )
        target["openfigi_exch_code"] = target.get("openfigi_exch_code") or exch_code
        if exch_code:
            target["exchange"] = exch_code
        target["instrument_type"] = (
            target.get("instrument_type")
            or match.get("securityType2")
            or match.get("securityType")
        )
        target["market_sector"] = target.get("market_sector") or match.get(
            "marketSector"
        )
        target["metadata_source"] = "OPENFIGI"

    @staticmethod
    def _needs_enrichment(row: dict[str, Any]) -> bool:
        return not row.get("figi") or not row.get("openfigi_exch_code")

    @staticmethod
    def _clean(value: Any) -> str | None:
        value = str(value or "").strip()
        return value or None

    @staticmethod
    def _base_symbol(value: Any) -> str:
        return str(value or "").strip().upper().split(".", 1)[0]

    @staticmethod
    def _normalize_text(value: Any) -> str:
        return re.sub(r"[^A-Z0-9]+", " ", str(value or "").upper()).strip()
