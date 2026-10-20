from market_data.capabilities import MarketDataCapability, ProviderCapabilities
from market_data.exceptions import InstrumentNotFoundError
from market_data.models import InstrumentProfile, InstrumentSearchResult
from providers.fmp import FMPProvider as LegacyFMPProvider


class FMPProvider:
    name = "fmp"
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                MarketDataCapability.SYMBOL_SEARCH,
                MarketDataCapability.PROFILE,
                MarketDataCapability.EXCHANGE,
            }
        )
    )

    def __init__(self):
        self.legacy = LegacyFMPProvider()

    def search(self, query, *, limit=20):
        return [
            InstrumentSearchResult(
                symbol=x["symbol"],
                name=x.get("name") or x["symbol"],
                currency=x.get("currency"),
                exchange_code=x.get("exchange"),
                exchange_name=x.get("exchangeFullName"),
                source=self.name,
            )
            for x in self.legacy.search_symbols(query, limit)
        ]

    def get_profile(self, instrument):
        if not instrument.symbol:
            raise InstrumentNotFoundError("FMP requires symbol")
        p = self.legacy.get_profile(instrument.symbol)
        if not p:
            raise InstrumentNotFoundError(instrument.symbol)
        return InstrumentProfile(
            symbol=p["symbol"],
            name=p.get("name") or p["symbol"],
            instrument_type=instrument.instrument_type,
            currency=p.get("currency"),
            country_code=p.get("country"),
            exchange_code=p.get("exchange"),
            exchange_name=p.get("exchangeFullName"),
            sector=p.get("sector"),
            website=p.get("website"),
            source=self.name,
        )
