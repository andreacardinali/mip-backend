from datetime import date
from decimal import Decimal
from xml.etree import ElementTree

from http_client import (
    HttpClient,
    HttpRequestError,
    HttpStatusError,
    RetryPolicy,
    create_http_client,
    request_with_retry,
)
from market_data.capabilities import MarketDataCapability, ProviderCapabilities
from market_data.exceptions import (
    InvalidProviderResponseError,
    ProviderRateLimitError,
    ProviderTemporaryError,
)
from market_data.models import ExchangeRateSnapshot

ECB_DAILY_RATES_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"


class EcbProvider:
    name = "ecb"
    capabilities = ProviderCapabilities(
        supported=frozenset({MarketDataCapability.EXCHANGE_RATES})
    )

    def __init__(
        self,
        *,
        timeout: float = 30.0,
        retry_policy: RetryPolicy | None = None,
        client: HttpClient | None = None,
    ) -> None:
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client

    def get_exchange_rates(self) -> ExchangeRateSnapshot:
        owns_client = self._client is None
        client = self._client or create_http_client(
            timeout=self.timeout,
            follow_redirects=True,
        )

        try:
            response = request_with_retry(
                client,
                "GET",
                ECB_DAILY_RATES_URL,
                policy=self.retry_policy,
            )
            return self._parse_xml(response.content)
        except HttpStatusError as error:
            status = error.response.status_code
            if status == 429:
                raise ProviderRateLimitError("ECB rate limit exceeded") from error
            raise ProviderTemporaryError(
                f"ECB request failed: HTTP {status}"
            ) from error
        except HttpRequestError as error:
            raise ProviderTemporaryError(f"ECB transport error: {error}") from error
        finally:
            if owns_client:
                client.close()

    @staticmethod
    def _parse_xml(payload: bytes) -> ExchangeRateSnapshot:
        try:
            root = ElementTree.fromstring(payload)
            time_node = root.find(".//{*}Cube[@time]")
            if time_node is None:
                raise ValueError("reference date is missing")

            reference_date = date.fromisoformat(time_node.attrib["time"])
            eur_rates = {"EUR": Decimal("1")}

            for node in time_node.findall("{*}Cube"):
                currency = node.attrib.get("currency")
                rate = node.attrib.get("rate")
                if currency and rate:
                    eur_rates[currency.strip().upper()] = Decimal(rate)

            usd_per_eur = eur_rates.get("USD")
            if usd_per_eur is None:
                raise ValueError("USD rate is missing")

            rates_to_usd = {
                currency: usd_per_eur / units for currency, units in eur_rates.items()
            }
            rates_to_usd["USD"] = Decimal("1")

            return ExchangeRateSnapshot(
                source="ECB",
                reference_date=reference_date,
                base_currency="EUR",
                rates_to_usd=rates_to_usd,
            )
        except (ElementTree.ParseError, KeyError, ValueError) as error:
            raise InvalidProviderResponseError(
                f"Invalid ECB exchange-rate response: {error}"
            ) from error
