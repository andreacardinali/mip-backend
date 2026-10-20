from providers.fmp import FMPProvider
from providers.ghostfolio import GhostfolioProvider


class MarketDataManager:

    def __init__(self):

        self.ghostfolio = GhostfolioProvider()

        self.fmp = FMPProvider()

    def search_symbols(self, query, limit=20):

        return self.ghostfolio.search_symbols(query, limit)

    def get_profile(
        self,
        symbol,
        data_source="YAHOO",
        full_refresh=False,
        require_price=True,
        **kwargs,
    ):

        profile = self.ghostfolio.refresh_and_get_profile(
            symbol,
            data_source=data_source,
            require_price=require_price,
            **kwargs,
        )

        if not profile:
            return None

        if full_refresh:

            try:

                metadata = self.fmp.find_symbol_metadata(symbol)

                if metadata:

                    profile["exchange"] = metadata.get("exchange")

                    profile["exchangeFullName"] = metadata.get("exchangeFullName")

            except Exception as ex:

                print(f"FMP enrichment failed for {symbol}: {ex}")

        return profile


market_data = MarketDataManager()
