from datetime import date
from decimal import Decimal
from xml.etree import ElementTree

import httpx

from http_client.retry import RetryPolicy, request_with_retry
from providers.exchange_rates.base import ExchangeRateSnapshot

ECB_DAILY_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


class EcbExchangeRateProvider:
    def __init__(self, *, timeout: float = 30.0, retry_policy: RetryPolicy | None = None) -> None:
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()

    def get_latest_rates(self) -> ExchangeRateSnapshot:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = request_with_retry(client, "GET", ECB_DAILY_RATES_URL, policy=self.retry_policy)
        return self._parse_xml(response.content)

    def _parse_xml(self, payload: bytes) -> ExchangeRateSnapshot:
        root = ElementTree.fromstring(payload)
        time_node = root.find(".//{*}Cube[@time]")
        if time_node is None:
            raise ValueError("ECB response does not contain a reference date")
        reference_date = date.fromisoformat(time_node.attrib["time"])
        eur_rates = {"EUR": Decimal("1")}
        for node in time_node.findall("{*}Cube"):
            currency, rate = node.attrib.get("currency"), node.attrib.get("rate")
            if currency and rate:
                eur_rates[currency.strip().upper()] = Decimal(rate)
        usd_per_eur = eur_rates.get("USD")
        if usd_per_eur is None:
            raise ValueError("ECB response does not contain USD")
        rates_to_usd = {currency: usd_per_eur / units for currency, units in eur_rates.items()}
        rates_to_usd["USD"] = Decimal("1")
        return ExchangeRateSnapshot(source="ECB", reference_date=reference_date, base_currency="EUR", rates_to_usd=rates_to_usd)
