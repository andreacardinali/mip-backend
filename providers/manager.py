from market_data import InstrumentReference, InstrumentType
from market_data.factory import market_data as service


class MarketDataManager:
    def search_symbols(self, query, limit=20):
        return [row.to_dict() for row in service.search(query, limit=limit)]

    def get_profile(
        self,
        symbol,
        data_source="YAHOO",
        full_refresh=False,
        require_price=True,
        **kwargs,
    ):
        profile = service.get_profile(
            InstrumentReference(symbol=symbol, instrument_type=InstrumentType.UNKNOWN),
            required_fields={"name"},
            include_quote=require_price,
        )
        return profile.to_import_payload() if profile else None


market_data = MarketDataManager()
