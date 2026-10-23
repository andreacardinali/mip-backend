import httpx

from http_client.retry import RetryPolicy, request_with_retry
from market_data.capabilities import MarketDataCapability, ProviderCapabilities
from market_data.exceptions import InstrumentNotFoundError
from market_data.models import InstrumentProfile
from market_data.providers.base import MarketDataProvider


class OpenFigiProvider(MarketDataProvider):
    name = "openfigi"
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                MarketDataCapability.PROFILE,
                MarketDataCapability.IDENTIFIERS,
                MarketDataCapability.EXCHANGE,
            }
        )
    )

    def __init__(
        self,
        api_key=None,
        *,
        base_url="https://api.openfigi.com/v3",
        timeout=15.0,
        retry_policy=None,
        client=None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.retry_policy = retry_policy or RetryPolicy()
        self._client = client

    def get_profile(self, instrument):
        if instrument.figi:
            job = {
                "idType": "ID_BB_GLOBAL",
                "idValue": instrument.figi,
            }
        elif instrument.isin:
            job = {
                "idType": "ID_ISIN",
                "idValue": instrument.isin,
            }
        elif instrument.symbol:
            job = {
                "idType": "TICKER",
                "idValue": instrument.symbol.split(".")[0],
            }
        else:
            raise InstrumentNotFoundError("OpenFIGI requires identifier")

        owns_client = self._client is None
        client = self._client or httpx.Client(timeout=self.timeout)
        headers = {"Content-Type": "application/json"}

        if self.api_key:
            headers["X-OPENFIGI-APIKEY"] = self.api_key

        try:
            response = request_with_retry(
                client,
                "POST",
                self.base_url + "/mapping",
                policy=self.retry_policy,
                json=[job],
                headers=headers,
                retry_non_idempotent=True,
            )
            payload = response.json()
        finally:
            if owns_client:
                client.close()

        data = (payload or [{}])[0].get("data") or []
        if not data:
            raise InstrumentNotFoundError(instrument.identifier)

        row = data[0]
        return InstrumentProfile(
            symbol=instrument.symbol or row.get("ticker") or instrument.identifier,
            name=row.get("name") or instrument.symbol or instrument.identifier,
            instrument_type=instrument.instrument_type,
            figi=row.get("figi"),
            figi_composite=row.get("compositeFIGI"),
            figi_share_class=row.get("shareClassFIGI"),
            market_sector=row.get("marketSector"),
            source=self.name,
        )
