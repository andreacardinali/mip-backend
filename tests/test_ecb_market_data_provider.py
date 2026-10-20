from decimal import Decimal

import httpx

from http_client.retry import RetryPolicy
from market_data.capabilities import MarketDataCapability
from market_data.providers.ecb import EcbProvider

ECB_XML = b"""<?xml version="1.0" encoding="UTF-8"?>
<Envelope xmlns="urn:gesmes">
  <Cube>
    <Cube time="2026-10-20">
      <Cube currency="USD" rate="1.1000"/>
      <Cube currency="CHF" rate="0.9500"/>
    </Cube>
  </Cube>
</Envelope>
"""


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_ecb_declares_exchange_rate_capability():
    assert EcbProvider.capabilities.supports(MarketDataCapability.EXCHANGE_RATES)
    assert not EcbProvider.capabilities.supports(MarketDataCapability.LIVE_PRICE)


def test_ecb_normalizes_rates_to_usd():
    def handler(request):
        return httpx.Response(
            200,
            request=request,
            content=ECB_XML,
        )

    provider = EcbProvider(
        client=_client(handler),
        retry_policy=RetryPolicy(max_retries=0),
    )
    snapshot = provider.get_exchange_rates()

    assert snapshot.source == "ECB"
    assert snapshot.reference_date.isoformat() == "2026-10-20"
    assert snapshot.base_currency == "EUR"
    assert snapshot.rates_to_usd["USD"] == Decimal("1")
    assert snapshot.rates_to_usd["EUR"] == Decimal("1.1000")
    assert snapshot.rates_to_usd["CHF"] == Decimal("1.1000") / Decimal("0.9500")
