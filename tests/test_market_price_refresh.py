from datetime import UTC, datetime

from market_data.models import BatchResult, MarketQuote
from services import market_price_refresh as service


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