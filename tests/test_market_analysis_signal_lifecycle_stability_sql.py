from pathlib import Path


def test_lifecycle_uses_processing_time_and_deduplicates_observations() -> None:
    sql = Path(
        "sql/20261026_market_analysis_signal_lifecycle_stability_fix.sql"
    ).read_text()

    assert "v_generation.created_at" in sql
    assert "clock_timestamp()" in sql
    assert "event_type', 'UNCHANGED'" in sql
    assert "v_previous.observed_at = v_signal.observed_at" in sql
    assert "v_generation.observed_at" not in sql
