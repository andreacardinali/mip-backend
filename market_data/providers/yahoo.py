from urllib.parse import quote

import httpx

from market_data.models import PriceBar


YAHOO_CHART_BASE_URL = (
    "https://query1.finance.yahoo.com/v8/finance/chart"
)


class YahooMarketDataProvider:
    name = "yahoo"

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout = timeout
        self._client = client

    def get_history(
        self,
        symbol: str,
        *,
        period: str,
        interval: str,
    ) -> list[PriceBar]:
        url = (
            f"{YAHOO_CHART_BASE_URL}/"
            f"{quote(symbol, safe='')}"
        )
        owns_client = self._client is None
        client = self._client or httpx.Client(
            timeout=self.timeout,
            headers={
                "User-Agent": "Mozilla/5.0 mip-market-data",
            },
        )

        try:
            response = client.get(
                url,
                params={
                    "range": period,
                    "interval": interval,
                    "events": "div,splits",
                },
            )
            response.raise_for_status()
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
            description = error.get("description") or str(error)
            raise RuntimeError(
                f"Yahoo market-data error: {description}"
            )

        result = (chart.get("result") or [None])[0]
        if not result:
            return []

        timestamps = result.get("timestamp") or []
        quote_data = (
            (result.get("indicators") or {})
            .get("quote")
            or [{}]
        )[0]

        rows: list[PriceBar] = []
        for index, timestamp in enumerate(timestamps):
            values = {
                field: _value_at(
                    quote_data.get(field),
                    index,
                )
                for field in (
                    "open",
                    "high",
                    "low",
                    "close",
                    "volume",
                )
            }
            if any(
                values[field] is None
                for field in ("open", "high", "low", "close")
            ):
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
