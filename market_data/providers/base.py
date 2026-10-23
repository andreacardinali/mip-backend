from __future__ import annotations

from market_data.exceptions import CapabilityUnavailableError


class MarketDataProvider:
    """Uniform contract implemented by every market-data provider."""

    name = "provider"

    def get_profile(self, instrument):
        raise CapabilityUnavailableError(
            f"{self.name} does not support profile retrieval"
        )

    def refresh_profile(
        self,
        instrument,
        *,
        data_source: str | None = None,
    ) -> bool:
        """Request an upstream refresh; False means not applicable."""
        return False

    def get_quote(self, instrument):
        raise CapabilityUnavailableError(f"{self.name} does not support live prices")
