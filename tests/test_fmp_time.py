import json
import runpy
from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from jobs.fmp.normalizers import normalize_filings_8k, normalize_timestamp
from market_time.normalization import TemporalNormalizationError


def test_fmp_timestamps_preserve_utc_and_require_verified_timezone():
    assert normalize_timestamp("2026-07-01T12:00:00+02:00") == datetime(
        2026, 7, 1, 10, tzinfo=UTC
    )
    assert normalize_timestamp("2026-07-01 12:00:00") is None
    assert normalize_timestamp("2026-07-01") is None
    assert normalize_timestamp("invalid") is None
    assert normalize_timestamp(
        "2026-07-01 12:00:00", source_timezone="UTC"
    ) == datetime(2026, 7, 1, 12, tzinfo=UTC)


def test_filings_log_temporal_rejections_and_deduplicate_instants(caplog):
    row = {
        "symbol": "TEST",
        "cik": "123",
        "formType": "8-K",
        "filingDate": "2026-07-01",
        "acceptedDate": "2026-07-01T12:00:00Z",
    }
    result = normalize_filings_8k(
        [
            row,
            {**row, "acceptedDate": "2026-07-01T14:00:00+02:00"},
            {**row, "acceptedDate": "2026-07-01 12:00:00"},
        ]
    )
    assert len(result) == 1
    assert result[0]["accepted_at"] == datetime(2026, 7, 1, 12, tzinfo=UTC)
    assert "rejected timestamps count=1" in caplog.text


@pytest.fixture
def sync_module():
    with patch.dict(
        "sys.modules",
        {
            "db": Mock(),
            "jobs.fmp.client": Mock(),
        },
    ):
        return runpy.run_path(
            str(
                Path(__file__).resolve().parents[1]
                / "jobs"
                / "fmp"
                / "fmp_daily_sync.py"
            )
        )


def test_date_only_catalyst_has_no_fabricated_instant(sync_module):
    db = Mock()
    row = {"symbol": "TEST", "entity_date": date(2026, 7, 1), "payload": {}}
    assert sync_module["_upsert_catalysts"](db, [row]) == 1
    params = db.execute.call_args.args[1]
    assert params["entity_date"] is None
    assert json.loads(params["payload"])["date"] == "2026-07-01"
    assert row["payload"] == {}


def test_persistence_rejects_naive_instants_before_database(sync_module):
    db = Mock()
    for function, field in (
        ("_upsert_catalysts", "entity_date"),
        ("_upsert_filings", "accepted_at"),
    ):
        with pytest.raises(TemporalNormalizationError):
            sync_module[function](db, [{field: datetime(2026, 7, 1), "payload": {}}])
    db.execute.assert_not_called()


def test_filings_persist_explicit_timestamptz(sync_module):
    db = Mock()
    instant = datetime(2026, 7, 1, tzinfo=UTC)
    sync_module["_upsert_filings"](db, [{"accepted_at": instant, "payload": {}}])
    statement, params = db.execute.call_args.args
    assert "CAST(:accepted_at AS timestamptz)" in str(statement)
    assert params["accepted_at"] == instant
