from urllib.parse import quote

import httpx

from http_client.retry import RetryPolicy, request_with_retry
from market_data.models import PriceBar

YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"


class YahooMarketDataProvider:
    name = "yahoo"

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client

    def get_history(self, symbol: str, *, period: str, interval: str) -> list[PriceBar]:
        url = f"{YAHOO_CHART_BASE_URL}/{quote(symbol, safe='')}"
        owns_client = self._client is None
        client = self._client or httpx.Client(
            timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0 mip-market-data"}
        )
        try:
            response = request_with_retry(
                client,
                "GET",
                url,
                policy=self.retry_policy,
                params={"range": period, "interval": interval, "events": "div,splits"},
            )
            payload = response.json()
        finally:
            if owns_client:
                client.close()
        return self._parse_history(payload)

    @staticmethod
    def _parse_history(payload: dict) -> list[PriceBar]:
        chart = payload.get("chart") or {}
        error = chart.get("error")
        if error:
            raise RuntimeError(
                f"Yahoo market-data error: {error.get('description') or str(error)}"
            )
        result = (chart.get("result") or [None])[0]
        if not result:
            return []
        timestamps = result.get("timestamp") or []
        quote_data = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        rows = []
        for index, timestamp in enumerate(timestamps):
            values = {
                field: _value_at(quote_data.get(field), index)
                for field in ("open", "high", "low", "close", "volume")
            }
            if any(values[field] is None for field in ("open", "high", "low", "close")):
                continue
            rows.append(
                PriceBar(
                    timestamp=int(timestamp),
                    open=float(values["open"]),
                    high=float(values["high"]),
                    low=float(values["low"]),
                    close=float(values["close"]),
                    volume=(
                        float(values["volume"])
                        if values["volume"] is not None
                        else None
                    ),
                )
            )
        return rows


def _value_at(values: list | None, index: int):
    if not values or index >= len(values):
        return None
    return values[index]
