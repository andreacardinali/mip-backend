from datetime import UTC, datetime
from pathlib import Path

from market_data.models import BatchResult, MarketQuote
from services import market_price_refresh as service


def test_scheduled_refresh_manifests_load_with_expected_cadence():
    from scheduler_bootstrap import load_job_definition

    jobs_root = Path(__file__).resolve().parents[1] / "jobs"
    dispatcher = load_job_definition(
        jobs_root / "market_price_refresh.job.json", timezone="UTC"
    )
    discovery = load_job_definition(
        jobs_root / "market_price_refresh_discovery.job.json", timezone="UTC"
    )

    assert dispatcher["trigger"]["interval_minutes"] == 1
    assert discovery["trigger"]["interval_minutes"] == 1


def test_live_price_max_age_seconds_setting(monkeypatch):
    monkeypatch.setattr(service.settings, "get", lambda key, default=None: default)
    assert service.live_price_max_age_seconds() == 30

    monkeypatch.setattr(service.settings, "get", lambda key, default=None: "120")
    assert service.live_price_max_age_seconds() == 120

    monkeypatch.setattr(service.settings, "get", lambda key, default=None: "0")
    try:
        service.live_price_max_age_seconds()
    except ValueError as error:
        assert "MARKET_PRICE_REFRESH_MAX_AGE_SECONDS" in str(error)
    else:
        raise AssertionError("non-positive max age must be rejected")


def test_price_refresh_batches_quotes_and_retries_only_failed_item(monkeypatch):
    jobs = [
        {
            "job_id": f"job-{symbol}",
            "instrument_id": f"instrument-{symbol}",
            "symbol": symbol,
            "data_source": "TEST",
            "attempt_count": 1,
            "max_attempts": 3,
        }
        for symbol in ("GOOD", "BAD", "NEXT")
    ]
    references = []
    persisted = []
    retried = []

    class ClaimSession:
        def commit(self):
            pass

        def rollback(self):
            pass

        def close(self):
            pass

    class MarketData:
        def get_quote_batch(self, items):
            references.extend(items)
            return [
                BatchResult(value=MarketQuote(int(datetime.now(UTC).timestamp()), 10.0)),
                BatchResult(error=RuntimeError("quote unavailable")),
                BatchResult(value=MarketQuote(int(datetime.now(UTC).timestamp()), 12.0)),
            ]

    monkeypatch.setattr(service, "SessionLocal", ClaimSession)
    monkeypatch.setattr(service, "_claim_due_jobs", lambda db, worker_id: jobs)
    monkeypatch.setattr(service, "market_data", MarketData())
    monkeypatch.setattr(
        service,
        "_persist_quote_and_complete",
        lambda job, quote: persisted.append(job["symbol"]),
    )
    monkeypatch.setattr(
        service,
        "_record_failure",
        lambda job, error_message: retried.append((job["symbol"], error_message))
        or "RETRY",
    )

    result = service.process_price_refresh_queue()

    assert [reference.symbol for reference in references] == ["GOOD", "BAD", "NEXT"]
    assert persisted == ["GOOD", "NEXT"]
    assert [symbol for symbol, _ in retried] == ["BAD"]
    assert result == {
        "status": "PARTIAL",
        "claimed": 3,
        "completed": 2,
        "retried": 1,
        "failed": 0,
        "errors": [
            {
                "job_id": "job-BAD",
                "symbol": "BAD",
                "attempt": 1,
                "status": "RETRY",
                "error": "quote unavailable",
            }
        ],
    }


def test_scheduled_worker_only_processes_queue(monkeypatch):
    from jobs.market_price_refresh import worker

    events = []

    monkeypatch.setattr(
        worker,
        "process_price_refresh_queue",
        lambda: events.append("process")
        or {"status": "SUCCESS", "claimed": 6},
    )

    result = worker.run()

    assert events == ["process"]
    assert result == {"status": "SUCCESS", "claimed": 6}


def test_scheduled_discovery_commits_enqueued_candidates(monkeypatch):
    from jobs.market_price_refresh import discovery

    events = []

    class DiscoverySession:
        def commit(self):
            events.append("commit")

        def rollback(self):
            events.append("rollback")

        def close(self):
            events.append("close")

    monkeypatch.setattr(discovery, "SessionLocal", DiscoverySession)
    monkeypatch.setattr(
        discovery,
        "enqueue_stale_market_price_refreshes",
        lambda db: events.append("discover")
        or {"candidates_count": 8, "enqueued_count": 6},
    )

    result = discovery.run()

    assert events == ["discover", "commit", "close"]
    assert result == {"candidates_count": 8, "enqueued_count": 6}