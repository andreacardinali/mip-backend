from datetime import timedelta
from unittest.mock import MagicMock

import pytest

from scripts import cleanup_llm_queue


@pytest.mark.parametrize("dry_run", [True, False])
@pytest.mark.parametrize("purge_all", [True, False])
def test_call_cleanup_selects_function_with_only_input_arguments(dry_run, purge_all):
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = {
        "p_completed_count": 1,
        "p_failed_count": 2,
        "p_abandoned_count": 3,
        "p_orphaned_pending_count": 4,
        "p_expired_processing_count": 5,
        "p_purged_count": 6 if purge_all else 0,
    }

    result = cleanup_llm_queue.call_cleanup(
        connection,
        completed_days=7,
        failed_days=30,
        abandoned_days=30,
        include_orphans=True,
        orphan_age=timedelta(hours=1),
        queue_key="test-queue",
        dry_run=dry_run,
        purge_all=purge_all,
    )

    statement, parameters = cursor.execute.call_args.args
    assert "SELECT * FROM public.sp_cleanup_llm_request_queue(" in statement
    assert "CALL" not in statement
    assert statement.count("%s") == 8
    assert parameters == (
        7,
        30,
        30,
        True,
        timedelta(hours=1),
        "test-queue",
        dry_run,
        purge_all,
    )
    assert result == cleanup_llm_queue.CleanupResult(
        1, 2, 3, 4, 5, 6 if purge_all else 0
    )
    assert result.total_count == (6 if purge_all else 15)
    connection.commit.assert_not_called()


def test_call_cleanup_rejects_missing_result():
    connection = MagicMock()
    cursor = connection.cursor.return_value.__enter__.return_value
    cursor.fetchone.return_value = None

    with pytest.raises(RuntimeError, match="Cleanup function returned no result"):
        cleanup_llm_queue.call_cleanup(
            connection,
            completed_days=7,
            failed_days=30,
            abandoned_days=30,
            include_orphans=False,
            orphan_age=timedelta(hours=1),
            queue_key=None,
            dry_run=True,
            purge_all=False,
        )
