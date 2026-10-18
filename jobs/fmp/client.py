import logging
import time
from typing import Any

import httpx

from config.settings import settings

logger = logging.getLogger(__name__)


class FMPClient:

    def __init__(self):
        self.api_key = settings.FMP_API_KEY
        self.base_url = settings.get(
            "FMP_BASE_URL",
            "https://financialmodelingprep.com/stable",
        ).rstrip("/")
        self.timeout = float(
            settings.get("FMP_HTTP_TIMEOUT", 30)
        )
        self.max_retries = int(
            settings.get("FMP_HTTP_MAX_RETRIES", 3)
        )
        self.retry_delay = float(
            settings.get("FMP_HTTP_RETRY_DELAY", 2)
        )
        self._client = httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "mip-scheduler/1.0",
            },
        )

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def get_list(
        self,
        endpoint: str,
        params: dict[str, Any],
    ) -> list[dict[str, Any]]:
        request_params = {
            **params,
            "apikey": self.api_key,
        }

        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                logger.info(
                    "FMP request endpoint=%s attempt=%s",
                    endpoint,
                    attempt,
                )

                response = self._client.get(
                    endpoint,
                    params=request_params,
                )
                response.raise_for_status()
                payload = response.json()

                if not isinstance(payload, list):
                    raise ValueError(
                        f"Unexpected FMP response for {endpoint}: "
                        f"expected list, got {type(payload).__name__}"
                    )

                return [
                    row
                    for row in payload
                    if isinstance(row, dict)
                ]

            except (
                httpx.HTTPError,
                ValueError,
            ) as exc:
                last_error = exc

                if attempt >= self.max_retries:
                    break

                time.sleep(
                    self.retry_delay * attempt
                )

        raise RuntimeError(
            f"FMP request failed for {endpoint}"
        ) from last_error

    def earnings_calendar(
        self,
        date_from: str,
        date_to: str,
    ) -> list[dict[str, Any]]:
        return self.get_list(
            "/earnings-calendar",
            {
                "from": date_from,
                "to": date_to,
            },
        )

    def dividends_calendar(
        self,
        date_from: str,
        date_to: str,
    ) -> list[dict[str, Any]]:
        return self.get_list(
            "/dividends-calendar",
            {
                "from": date_from,
                "to": date_to,
            },
        )

    def splits_calendar(
        self,
        date_from: str,
        date_to: str,
    ) -> list[dict[str, Any]]:
        return self.get_list(
            "/splits-calendar",
            {
                "from": date_from,
                "to": date_to,
            },
        )

    def filings_8k(
        self,
        date_from: str,
        date_to: str,
        page: int = 0,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        return self.get_list(
            "/sec-filings-8k",
            {
                "from": date_from,
                "to": date_to,
                "page": page,
                "limit": limit,
            },
        )
