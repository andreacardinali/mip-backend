from db import SessionLocal
from job_runtime.requests import current_job_request
from services.entities import refresh_entities
from services.entity_refresh_request import RefreshEntitiesRequest


def run() -> dict:
    execution = current_job_request.get()
    request = RefreshEntitiesRequest.model_validate(
        execution.get("payload") or {} if execution else {}
    )
    db = SessionLocal()
    try:
        return refresh_entities(
            db,
            mode=request.mode,
            symbol=request.symbol,
            create_if_missing=request.create_if_missing,
            full_refresh=request.full_refresh,
        )
    finally:
        db.close()
