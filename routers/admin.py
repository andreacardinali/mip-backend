from fastapi import APIRouter, Depends

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from scheduler_repository import SchedulerRepository
from services.entity_refresh_request import RefreshEntitiesRequest

router = APIRouter()


@router.post("/cache/cleanup", status_code=202)
async def cleanup_cache(
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()

    try:
        result = SchedulerRepository(db).request_run(
            job_key="market_cache_cleanup",
            requested_by=current_user["user_id"],
            payload={},
            source="CACHE_UI",
        )
        db.commit()
        return result

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


@router.post(
    "/entities/refresh",
    status_code=202,
    summary="Aggiorna le entities",
    description=("Aggiorna una o più entities in base alla modalità richiesta."),
)
def refresh_entities_endpoint(
    payload: RefreshEntitiesRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()

    try:
        result = SchedulerRepository(db).request_run(
            job_key="refresh_entities",
            requested_by=current_user["user_id"],
            payload=payload.model_dump(mode="json"),
            source="ENTITIES_UI",
        )
        db.commit()
        return result

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
