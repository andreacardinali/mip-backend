from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from scheduler_repository import SchedulerRepository

router = APIRouter()


@router.get("/runs/{job_run_id}")
def execution_status(
    job_run_id: UUID, current_user: CurrentUser = Depends(get_current_user)
):
    db = SessionLocal()
    try:
        result = SchedulerRepository(db).request_status(
            job_run_id=str(job_run_id), user_id=current_user["user_id"]
        )
        if result is None:
            raise HTTPException(404, "Execution not found")
        return result
    finally:
        db.close()
