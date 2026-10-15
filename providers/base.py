from abc import ABC
from abc import abstractmethod


class MarketDataProvider(ABC):

    @abstractmethod
    def search_symbols(
        self,
        query: str,
        limit: int = 20
    ):
        pass

    @abstractmethod
    def get_profile(
        self,
        symbol: str,
        **kwargs
    ):
        pass
