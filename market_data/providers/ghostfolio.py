from market_data.capabilities import (
    InstrumentType,
    MarketDataCapability,
    ProviderCapabilities,
)
from market_data.exceptions import InstrumentNotFoundError
from market_data.models import InstrumentProfile, InstrumentSearchResult, MarketQuote
from providers.ghostfolio import GhostfolioProvider as LegacyGhostfolioProvider


class GhostfolioProvider:
    name = "ghostfolio"
    capabilities = ProviderCapabilities(
        supported=frozenset(
            {
                MarketDataCapability.SYMBOL_SEARCH,
                MarketDataCapability.PROFILE,
                MarketDataCapability.IDENTIFIERS,
                MarketDataCapability.LIVE_PRICE,
            }
        )
    )

    def __init__(self):
        self.legacy = LegacyGhostfolioProvider()

    def search(self, query, *, limit=20):
        return [
            InstrumentSearchResult(
                symbol=x["symbol"],
                name=x.get("name") or x["symbol"],
                instrument_type=_kind(x.get("asset_sub_class")),
                currency=x.get("currency"),
                source=self.name,
            )
            for x in self.legacy.search_symbols(query, limit)
        ]

    def get_profile(self, instrument):
        if not instrument.symbol:
            raise InstrumentNotFoundError("Ghostfolio requires symbol")
        p = self.legacy.refresh_and_get_profile(instrument.symbol, require_price=False)
        if not p:
            raise InstrumentNotFoundError(instrument.symbol)
        return InstrumentProfile(
            symbol=p["symbol"],
            name=p.get("name") or p["symbol"],
            instrument_type=_kind(p.get("asset_sub_class")),
            currency=p.get("currency"),
            country_code=p.get("country_code"),
            figi=p.get("figi"),
            figi_composite=p.get("figi_composite"),
            figi_share_class=p.get("figi_share_class"),
            sector=p.get("sector"),
            website=p.get("website"),
            last_price=p.get("last_price"),
            source=self.name,
        )

    def get_quote(self, instrument):
        p = self.get_profile(instrument)
        if p.last_price is None:
            raise InstrumentNotFoundError(instrument.identifier)
        import time

        return MarketQuote(
            timestamp=int(time.time()),
            price=p.last_price,
            currency=p.currency,
            source=self.name,
        )


def _kind(value):
    value = str(value or "").upper()
    return {
        "ETF": InstrumentType.ETF,
        "ETP": InstrumentType.ETP,
        "INDEX": InstrumentType.INDEX,
        "CRYPTOCURRENCY": InstrumentType.CRYPTO,
    }.get(value, InstrumentType.STOCK)
