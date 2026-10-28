from job_runtime.database import run_transactional
from services.entities import RefreshMode, refresh_entities


def run() -> dict:
    return run_transactional(
        lambda db: refresh_entities(
            db,
            mode=RefreshMode.ALL,
            symbol=None,
            create_if_missing=False,
            full_refresh=True,
        )
    )
