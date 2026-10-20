from scripts.refresh_entities import _progress_printer


def test_progress_printer_accepts_entity_event(capsys):
    _progress_printer(
        {
            "event": "entity_updated",
            "index": 1,
            "total": 2,
            "symbol": "LDO.MI",
            "duration_ms": 123,
            "updated": 1,
            "skipped": 0,
            "failed": 0,
        }
    )
    output = capsys.readouterr().out
    assert "[1/2] OK LDO.MI" in output
    assert "duration=123ms" in output
