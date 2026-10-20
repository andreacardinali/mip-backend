from urllib.parse import quote

import httpx

from http_client.retry import RetryPolicy, request_with_retry
from market_data.capabilities import (
    InstrumentType,
    MarketDataCapability,
    ProviderCapabilities,
)
from market_data.exceptions import InstrumentNotFoundError
from market_data.models import HistoryRequest, MarketQuote, PriceBar, PricePoint

YAHOO_CHART_BASE_URL = "https://query1.finance.yahoo.com/v8/finance/chart"
_ALL = frozenset(
    {
        InstrumentType.STOCK,
        InstrumentType.ETF,
        InstrumentType.ETP,
        InstrumentType.FUND,
        InstrumentType.INDEX,
        InstrumentType.CRYPTO,
        InstrumentType.COMMODITY,
        InstrumentType.UNKNOWN,
    }
)


class YahooMarketDataProvider:
    name = "yahoo"
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                MarketDataCapability.LIVE_PRICE,
                MarketDataCapability.PRICE_HISTORY,
                MarketDataCapability.OHLCV,
            }
        ),
        instrument_types={
            MarketDataCapability.LIVE_PRICE: _ALL,
            MarketDataCapability.PRICE_HISTORY: _ALL,
            MarketDataCapability.OHLCV: _ALL,
        },
    )

    def __init__(self, *, timeout=30.0, retry_policy=None, client=None):
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client

    def get_quote(self, instrument):
        bars = self.get_ohlcv(
            HistoryRequest(instrument=instrument, period="5d", interval="1d")
        )
        if not bars:
            raise InstrumentNotFoundError(instrument.identifier)
        bar = bars[-1]
        return MarketQuote(
            timestamp=bar.timestamp,
            price=bar.close,
            currency=instrument.currency,
            source=self.name,
        )

    def get_price_history(self, request):
        return [
            PricePoint(
                timestamp=bar.timestamp,
                price=bar.close,
                currency=request.instrument.currency,
            )
            for bar in self.get_ohlcv(request)
        ]

    def get_history(self, symbol, *, period, interval):
        from market_data.models import InstrumentReference

        return self.get_ohlcv(
            HistoryRequest(
                InstrumentReference(symbol=symbol), period=period, interval=interval
            )
        )

    def get_ohlcv(self, request):
        symbol = request.instrument.symbol
        if not symbol:
            raise InstrumentNotFoundError("Yahoo requires symbol")
        url = f"{YAHOO_CHART_BASE_URL}/{quote(symbol, safe='')}"
        owns = self._client is None
        client = self._client or httpx.Client(
            timeout=self.timeout, headers={"User-Agent": "Mozilla/5.0 mip-market-data"}
        )
        params = {
            "range": request.period,
            "interval": request.interval,
            "events": "div,splits",
        }
        if request.from_timestamp is not None:
            params.pop("range", None)
            params["period1"] = request.from_timestamp
        if request.to_timestamp is not None:
            params.pop("range", None)
            params["period2"] = request.to_timestamp
        try:
            response = request_with_retry(
                client, "GET", url, policy=self.retry_policy, params=params
            )
            payload = response.json()
        finally:
            if owns:
                client.close()
        return self._parse_history(payload)

    @staticmethod
    def _parse_history(payload):
        chart = payload.get("chart") or {}
        if chart.get("error"):
            raise InstrumentNotFoundError(str(chart["error"]))
        result = (chart.get("result") or [None])[0]
        if not result:
            return []
        ts = result.get("timestamp") or []
        q = ((result.get("indicators") or {}).get("quote") or [{}])[0]
        rows = []
        for i, timestamp in enumerate(ts):
            vals = {
                k: _at(q.get(k), i) for k in ("open", "high", "low", "close", "volume")
            }
            if any(vals[k] is None for k in ("open", "high", "low", "close")):
                continue
            rows.append(
                PriceBar(
                    int(timestamp),
                    float(vals["open"]),
                    float(vals["high"]),
                    float(vals["low"]),
                    float(vals["close"]),
                    float(vals["volume"]) if vals["volume"] is not None else None,
                )
            )
        return rows


def _at(values, index):
    return None if not values or index >= len(values) else values[index]
