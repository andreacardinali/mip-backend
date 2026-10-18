from datetime import date
from decimal import Decimal
from xml.etree import ElementTree

import httpx

from providers.exchange_rates.base import ExchangeRateSnapshot


ECB_DAILY_RATES_URL = (
    "https://www.ecb.europa.eu/"
    "stats/eurofxref/eurofxref-daily.xml"
)


class EcbExchangeRateProvider:
    def __init__(self, *, timeout: float = 30.0) -> None:
        self.timeout = timeout

    def get_latest_rates(self) -> ExchangeRateSnapshot:
        response = httpx.get(
            ECB_DAILY_RATES_URL,
            timeout=self.timeout,
            follow_redirects=True,
        )
        response.raise_for_status()
        return self._parse_xml(response.content)

    def _parse_xml(self, payload: bytes) -> ExchangeRateSnapshot:
        root = ElementTree.fromstring(payload)
        time_node = root.find(".//{*}Cube[@time]")

        if time_node is None:
            raise ValueError(
                "ECB response does not contain a reference date"
            )

        reference_date = date.fromisoformat(time_node.attrib["time"])
        eur_rates: dict[str, Decimal] = {
            "EUR": Decimal("1"),
        }

        for node in time_node.findall("{*}Cube"):
            currency = node.attrib.get("currency")
            rate = node.attrib.get("rate")

            if not currency or not rate:
                continue

            eur_rates[currency.strip().upper()] = Decimal(rate)

        usd_per_eur = eur_rates.get("USD")

        if usd_per_eur is None:
            raise ValueError("ECB response does not contain USD")

        rates_to_usd = {
            currency: usd_per_eur / currency_units_per_eur
            for currency, currency_units_per_eur in eur_rates.items()
        }
        rates_to_usd["USD"] = Decimal("1")

        return ExchangeRateSnapshot(
            source="ECB",
            reference_date=reference_date,
            base_currency="EUR",
            rates_to_usd=rates_to_usd,
        )
