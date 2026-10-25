from __future__ import annotations

from market_data.capabilities import InstrumentType
from market_data.models import InstrumentProfile
from services import entities


def test_profile_payload_can_skip_quote(monkeypatch) -> None:
    captured = {}

    class FakeMarketData:
        def get_profile(self, reference, **kwargs):
            captured["reference"] = reference
            captured.update(kwargs)
            return InstrumentProfile(
                symbol="BSX",
                name="Boston Scientific",
                instrument_type=InstrumentType.STOCK,
            )

    monkeypatch.setattr(entities, "market_data", FakeMarketData())
    reference = entities.InstrumentReference(
        symbol="BSX",
        instrument_type=InstrumentType.STOCK,
    )

    payload = entities._profile_payload(
        reference,
        full_refresh=False,
        include_quote=False,
    )

    assert payload is not None
    assert captured["reference"].instrument_type == InstrumentType.STOCK
    assert captured["include_quote"] is False
