import logging
from typing import Any

import httpx

from config.settings import settings
from http_client.retry import RetryPolicy, request_with_retry

logger = logging.getLogger(__name__)


class FMPClient:
    def __init__(self):
        self.api_key = settings.FMP_API_KEY
        self.base_url = settings.get("FMP_BASE_URL", "https://financialmodelingprep.com/stable").rstrip("/")
        self.timeout = float(settings.get("FMP_HTTP_TIMEOUT", 30))
        self.max_retries = int(settings.get("FMP_HTTP_MAX_RETRIES", 3))
        self.retry_delay = float(settings.get("FMP_HTTP_RETRY_DELAY", 2))
        self.retry_policy = RetryPolicy(
            max_retries=self.max_retries,
            base_delay_seconds=self.retry_delay,
        )
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={"Accept": "application/json", "User-Agent": "mip-scheduler/1.0"},
        )

    def close(self): self._client.close()
    def __enter__(self): return self
    def __exit__(self, exc_type, exc_value, traceback): self.close()

    def get_list(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        logger.info("FMP request endpoint=%s", endpoint)
        response = request_with_retry(
            self._client,
            "GET",
            endpoint,
            policy=self.retry_policy,
            params={**params, "apikey": self.api_key},
        )
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(
                f"Unexpected FMP response for {endpoint}: expected list, got {type(payload).__name__}"
            )
        return [row for row in payload if isinstance(row, dict)]

    def earnings_calendar(self, date_from: str, date_to: str):
        return self.get_list("/earnings-calendar", {"from": date_from, "to": date_to})
    def dividends_calendar(self, date_from: str, date_to: str):
        return self.get_list("/dividends-calendar", {"from": date_from, "to": date_to})
    def splits_calendar(self, date_from: str, date_to: str):
        return self.get_list("/splits-calendar", {"from": date_from, "to": date_to})
    def filings_8k(self, date_from: str, date_to: str, page: int = 0, limit: int = 20):
        return self.get_list("/sec-filings-8k", {"from": date_from, "to": date_to, "page": page, "limit": limit})
