import logging
from dataclasses import fields
from datetime import UTC, datetime, timedelta

from market_data.capabilities import InstrumentType, MarketDataCapability
from market_data.config import MarketDataConfig
from market_data.exceptions import (
    AllProvidersFailedError,
    CapabilityUnavailableError,
    InstrumentNotFoundError,
    InvalidProviderResponseError,
    ProviderTemporaryError,
    StaleMarketPriceError,
)
from market_data.models import (
    ExchangeRateSnapshot,
    HistoryRequest,
    InstrumentProfile,
    InstrumentReference,
    InstrumentSearchResult,
)
from market_data.registry import ProviderRegistry

logger = logging.getLogger(__name__)


class MarketDataService:
    def __init__(self, registry: ProviderRegistry, config: MarketDataConfig) -> None:
        self.registry = registry
        self.config = config

    def capabilities(self) -> list[dict]:
        return self.registry.describe()

    def search(self, query: str, *, limit: int = 20) -> list[InstrumentSearchResult]:
        clean = str(query or "").strip()
        if not clean:
            return []
        return self._first(
            MarketDataCapability.SYMBOL_SEARCH,
            InstrumentType.UNKNOWN,
            lambda provider: provider.search(clean, limit=limit),
        )

    def get_profile(
        self,
        reference: InstrumentReference,
        *,
        required_fields: set[str] | None = None,
        include_quote: bool = False,
    ) -> InstrumentProfile | None:
        profiles = []
        failures = []
        capability = MarketDataCapability.PROFILE
        for provider in self._ordered(capability, reference.instrument_type):
            try:
                profile = provider.get_profile(reference)
                if profile:
                    profiles.append(profile)
                    merged = _merge_profiles(profiles)
                    if _has_fields(merged, required_fields or set()):
                        break
            except (
                CapabilityUnavailableError,
                InstrumentNotFoundError,
                InvalidProviderResponseError,
                ProviderTemporaryError,
                StaleMarketPriceError,
            ) as error:
                failures.append((provider.name, error))
                logger.info(
                    "Market data fallback capability=%s provider=%s error=%s",
                    capability.value,
                    provider.name,
                    error,
                )
            except Exception as error:
                failures.append((provider.name, error))
                logger.exception(
                    "Unexpected provider failure capability=%s provider=%s",
                    capability.value,
                    provider.name,
                )
        if not profiles:
            if failures:
                logger.info("No profile found failures=%s", failures)
            return None
        profile = _merge_profiles(profiles)
        if include_quote:
            quote = self.get_quote(reference, required=False)
            if quote:
                quote_source = quote.source or "unknown"
                profile = profile.with_fields(
                    last_price=quote.price,
                    price_timestamp=quote.timestamp,
                    provenance={
                        **profile.provenance,
                        "last_price": quote_source,
                        "price_timestamp": quote_source,
                    },
                )
        return profile

    def get_quote(self, reference: InstrumentReference, *, required: bool = True):
        return self._first(
            MarketDataCapability.LIVE_PRICE,
            reference.instrument_type,
            lambda provider: self._validated_quote(provider, reference),
            required=required,
        )

    def _validated_quote(self, provider, reference):
        quote = provider.get_quote(reference)
        if quote.timestamp is None:
            raise StaleMarketPriceError(
                f"{provider.name} returned quote without timestamp"
            )
        quote_time = datetime.fromtimestamp(int(quote.timestamp), tz=UTC)
        age = datetime.now(UTC) - quote_time
        if age > timedelta(hours=self.config.max_quote_age_hours):
            raise StaleMarketPriceError(
                f"{provider.name} stale quote for {reference.identifier}: "
                f"price_as_of={quote_time.isoformat()} "
                f"age_hours={age.total_seconds()/3600:.1f} "
                f"max_age_hours={self.config.max_quote_age_hours:.1f}"
            )
        return quote

    def get_price_history(self, request: HistoryRequest):
        return self._first(
            MarketDataCapability.PRICE_HISTORY,
            request.instrument.instrument_type,
            lambda provider: provider.get_price_history(request),
            interval=request.interval,
        )

    def get_ohlcv(self, request: HistoryRequest):
        return self._first(
            MarketDataCapability.OHLCV,
            request.instrument.instrument_type,
            lambda provider: provider.get_ohlcv(request),
            interval=request.interval,
        )

    def get_exchange_rates(
        self, *, required: bool = True
    ) -> ExchangeRateSnapshot | None:
        return self._first(
            MarketDataCapability.EXCHANGE_RATES,
            InstrumentType.UNKNOWN,
            lambda provider: provider.get_exchange_rates(),
            required=required,
        )

    def get_news(self, reference: InstrumentReference, *, limit: int = 20):
        return self._first(
            MarketDataCapability.NEWS,
            reference.instrument_type,
            lambda provider: provider.get_news(reference, limit=limit),
        )

    def get_etf_holdings(self, reference: InstrumentReference, *, limit=None):
        return self._first(
            MarketDataCapability.ETF_HOLDINGS,
            reference.instrument_type,
            lambda provider: provider.get_etf_holdings(reference, limit=limit),
        )

    def get_etf_countries(self, reference: InstrumentReference):
        return self._first(
            MarketDataCapability.ETF_HOLDINGS,
            reference.instrument_type,
            lambda provider: provider.get_etf_countries(reference),
            required=False,
        )

    def get_etf_sectors(self, reference: InstrumentReference):
        return self._first(
            MarketDataCapability.ETF_HOLDINGS,
            reference.instrument_type,
            lambda provider: provider.get_etf_sectors(reference),
            required=False,
        )

    def get_earnings_calendar(self, event_date, *, symbol=None):
        return self._first(
            MarketDataCapability.EARNINGS_CALENDAR,
            InstrumentType.UNKNOWN,
            lambda provider: provider.get_earnings_calendar(event_date, symbol=symbol),
        )

    def get_economic_calendar(self, *, limit=None):
        return self._first(
            MarketDataCapability.ECONOMIC_CALENDAR,
            InstrumentType.UNKNOWN,
            lambda provider: provider.get_economic_calendar(limit=limit),
        )

    def _ordered(self, capability, instrument_type, interval=None):
        result = []
        for name in self.config.provider_orders[capability]:
            try:
                provider = self.registry.get(name)
            except ValueError:
                logger.warning(
                    "Configured market-data provider is not registered capability=%s provider=%s",
                    capability.value,
                    name,
                )
                continue
            if provider.capabilities.supports(
                capability, instrument_type=instrument_type, interval=interval
            ):
                result.append(provider)
        return result

    def _first(
        self, capability, instrument_type, operation, *, interval=None, required=True
    ):
        failures = []
        for provider in self._ordered(capability, instrument_type, interval):
            try:
                value = operation(provider)
                if value is not None and value != []:
                    logger.debug(
                        "Market data resolved capability=%s provider=%s",
                        capability.value,
                        provider.name,
                    )
                    return value
                failures.append(
                    (provider.name, InstrumentNotFoundError("empty result"))
                )
            except (
                CapabilityUnavailableError,
                InstrumentNotFoundError,
                InvalidProviderResponseError,
                ProviderTemporaryError,
                StaleMarketPriceError,
            ) as error:
                failures.append((provider.name, error))
                logger.info(
                    "Market data fallback capability=%s provider=%s error=%s",
                    capability.value,
                    provider.name,
                    error,
                )
            except Exception as error:
                failures.append((provider.name, error))
                logger.exception(
                    "Unexpected provider failure capability=%s provider=%s",
                    capability.value,
                    provider.name,
                )
        if required:
            raise AllProvidersFailedError(capability.value, failures)
        return None


def _has_fields(profile: InstrumentProfile, required: set[str]) -> bool:
    return all(getattr(profile, field, None) is not None for field in required)


def _merge_profiles(profiles: list[InstrumentProfile]) -> InstrumentProfile:
    first = profiles[0]
    values = {}
    provenance = {}
    for descriptor in fields(InstrumentProfile):
        if descriptor.name == "provenance":
            continue
        value = None
        for profile in profiles:
            candidate = getattr(profile, descriptor.name)
            if candidate is not None and candidate != InstrumentType.UNKNOWN:
                value = candidate
                provenance[descriptor.name] = profile.source or "unknown"
                break
        values[descriptor.name] = value
    values["symbol"] = values["symbol"] or first.symbol
    values["name"] = values["name"] or first.name
    values["instrument_type"] = values["instrument_type"] or InstrumentType.UNKNOWN
    values["provenance"] = provenance
    return InstrumentProfile(**values)
