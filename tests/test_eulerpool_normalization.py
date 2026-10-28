from datetime import UTC, date, time
from unittest.mock import Mock

import pytest

from market_data.capabilities import InstrumentType
from market_data.models import InstrumentReference
from market_data.providers.eulerpool import EulerpoolProvider
from market_time.repository import SQLHolidayRepository
from scripts.apply_market_time_holidays import migration_statements
from scripts.sync_exchange_holidays import acquire


class Response:
    status_code = 200

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        pass


class Client:
    def __init__(self, responses):
        self.responses = iter(responses)

    def request(self, *args, **kwargs):
        return Response(next(self.responses))

    def close(self):
        pass


def test_etf_price_history_is_close_only():
    provider = EulerpoolProvider(
        "key",
        client=Client(
            [
                [
                    {
                        "ticker": "WSPX.MI",
                    }
                ],
                [
                    [1646179200000, 25.19],
                    [1646265600000, 25.20],
                ],
            ]
        ),
    )
    request = type(
        "R",
        (),
        {
            "instrument": InstrumentReference(
                symbol="WSPX.MI",
                isin="XS2427355958",
                instrument_type=InstrumentType.ETF,
            )
        },
    )()
    rows = provider.get_price_history(request)
    assert rows[0].timestamp == 1646179200
    assert rows[0].price == 25.19


def holiday_payload():
    return {
        "exchange": "NYSE",
        "as_of": "2026-10-27",
        "updated_at": "2026-10-27T09:18:08.919Z",
        "holidays": [
            {
                "date": "2025-12-24",
                "name": "Christmas Early Close",
                "exchange": "NYSE",
                "isClosed": None,
                "adjOpenTime": None,
                "adjCloseTime": "13:00",
                "isFullyClosed": False,
            },
            {
                "date": "2026-09-07",
                "name": "Labor Day",
                "exchange": "NYSE",
                "isClosed": True,
                "adjOpenTime": None,
                "adjCloseTime": None,
            },
        ],
    }


def test_holidays_identity_is_requested_code_not_response_label():
    client = Client([holiday_payload()])
    provider = EulerpoolProvider("key", client=client)
    snapshot = provider.get_exchange_holidays(" xnys ")
    assert snapshot.exchange_code == "XNYS"
    assert snapshot.provider_exchange == "NYSE"
    assert snapshot.as_of == date(2026, 10, 27)
    assert snapshot.updated_at.tzinfo == UTC
    assert snapshot.fetched_at.tzinfo == UTC
    assert snapshot.holidays[0].closes_at == time(13)
    assert snapshot.holidays[0].is_closed is False
    assert snapshot.holidays[1].is_closed is True
    assert not hasattr(snapshot, "coverage_end")


@pytest.mark.parametrize("defect", ["identity", "naive", "duplicate", "flags"])
def test_holidays_reject_invalid_snapshot(defect):
    payload = holiday_payload()
    if defect == "identity":
        payload["exchange_code"] = "XETR"
    elif defect == "naive":
        payload["updated_at"] = "2026-10-27T09:18:08"
    elif defect == "duplicate":
        payload["holidays"].append(payload["holidays"][0])
    else:
        payload["holidays"][1]["isFullyClosed"] = False
    provider = EulerpoolProvider("key", client=Client([payload]))
    with pytest.raises(ValueError):
        provider.get_exchange_holidays("XNYS")


def test_exchange_resolution_uses_catalog_without_aliases_or_partial_names():
    provider = EulerpoolProvider(
        "key",
        client=Client(
            [
                [
                    {
                        "exchange_code": "XFRA",
                        "name": "Frankfurt Stock Exchange",
                    },
                    {
                        "exchange_code": "XETR",
                        "name": "Xetra",
                    },
                ]
            ]
        ),
    )
    assert provider._resolve_exchange("xfra").code == "XFRA"
    assert provider._resolve_exchange("Frankfurt Stock Exchange").code == "XFRA"
    assert provider._resolve_exchange("Frankfurt") is None
    assert provider._resolve_exchange("xetra").code == "XETR"


def test_ambiguous_catalog_names_do_not_resolve():
    provider = EulerpoolProvider(
        "key",
        client=Client(
            [
                [
                    {
                        "code": "XFRA",
                        "name": "Germany",
                    },
                    {
                        "code": "XETR",
                        "name": "Germany",
                    },
                ]
            ]
        ),
    )
    assert provider._resolve_exchange("Germany") is None


def test_acquisition_verifies_requested_code_in_catalog():
    provider = EulerpoolProvider(
        "key",
        client=Client(
            [
                [{"code": "XNYS", "name": "New York Stock Exchange"}],
                holiday_payload(),
            ]
        ),
    )
    assert acquire(provider, "XNYS").exchange_code == "XNYS"
    with pytest.raises(ValueError, match="catalog"):
        acquire(provider, "XETR")


def test_holiday_repository_uses_stored_function_and_restores_snapshot():
    snapshot = EulerpoolProvider(
        "key", client=Client([holiday_payload()])
    ).get_exchange_holidays("XNYS")
    db = Mock()
    db.execute.return_value.scalar_one.return_value = "snapshot-id"
    repository = SQLHolidayRepository(db)
    assert repository.save("NYSE", snapshot) == "snapshot-id"
    statement, parameters = db.execute.call_args.args
    assert "sp_store_exchange_holidays" in str(statement)
    assert parameters["exchange"] == "NYSE"
    assert parameters["exchange_code"] == "XNYS"
    assert parameters["updated_at"].tzinfo == UTC
    db.execute.return_value.mappings.return_value.one_or_none.return_value = {
        "exchange_code": "XNYS",
        "source": "eulerpool",
        "raw_payload": holiday_payload(),
        "fetched_at": snapshot.fetched_at,
    }
    assert repository.latest("XNYS", "eulerpool") == snapshot


def test_holiday_migration_preserves_existing_exchanges_and_utc_types():
    statements = migration_statements()
    assert len(statements) == 4
    assert "timestamp with time zone" in statements[1]
    assert "as_of date" in statements[1]
    assert "UNIQUE (exchange_code, source, provider_updated_at)" in statements[1]
    assert "REFERENCES public.exchanges(exchange)" in statements[2]
    assert "INSERT INTO public.exchanges" not in statements[3]
    assert "exchange_source_mappings" in statements[3]
    assert "exchange_code <> p_exchange_code" in statements[3]
    assert "ON CONFLICT" in statements[3]
    assert "Provider exchange identity conflicts" in statements[3]
    assert "without changing its version" in statements[3]
    assert "CASCADE" not in "\n".join(statements)
