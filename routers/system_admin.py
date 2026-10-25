from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from auth.security import CurrentUser, require_admin
from db import SessionLocal
from scheduler import execute_job

router = APIRouter()


class JobUpdate(BaseModel):
    enabled: bool | None = None
    trigger_type: str | None = None
    cron_expression: str | None = None
    interval_minutes: int | None = None


@router.get("/scheduler/jobs")
def list_jobs(
    _: CurrentUser = Depends(require_admin),
):
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT * FROM sp_list_scheduler_jobs()")).mappings()
        return [dict(row) for row in rows]
    finally:
        db.close()


@router.patch("/scheduler/jobs/{job_key}")
def update_job(
    job_key: str,
    payload: JobUpdate,
    current_user: CurrentUser = Depends(require_admin),
):
    if payload.enabled is None and payload.trigger_type is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No scheduler job changes were provided",
        )

    db = SessionLocal()
    try:
        if payload.enabled is not None:
            db.execute(
                text(
                    """
                    SELECT *
                    FROM sp_set_scheduler_job_enabled(
                        CAST(:actor_user_id AS uuid),
                        :job_key,
                        :enabled
                    )
                    """
                ),
                {
                    "actor_user_id": current_user["user_id"],
                    "job_key": job_key,
                    "enabled": payload.enabled,
                },
            )

        if payload.trigger_type is not None:
            db.execute(
                text(
                    """
                    SELECT *
                    FROM sp_update_scheduler_job(
                        CAST(:actor_user_id AS uuid),
                        :job_key,
                        :trigger_type,
                        :cron_expression,
                        :interval_minutes
                    )
                    """
                ),
                {
                    "actor_user_id": current_user["user_id"],
                    "job_key": job_key,
                    "trigger_type": payload.trigger_type,
                    "cron_expression": payload.cron_expression,
                    "interval_minutes": payload.interval_minutes,
                },
            )

        db.commit()
        return {
            "status": "SUCCESS",
            "job_key": job_key,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    finally:
        db.close()


@router.post(
    "/scheduler/jobs/{job_key}/run",
    status_code=status.HTTP_202_ACCEPTED,
)
def run_job_now(
    job_key: str,
    background_tasks: BackgroundTasks,
    current_user: CurrentUser = Depends(require_admin),
):
    db = SessionLocal()
    try:
        row = (
            db.execute(
                text(
                    """
                SELECT *
                FROM sp_get_scheduler_job(:job_key)
                """
                ),
                {"job_key": job_key},
            )
            .mappings()
            .one_or_none()
        )

        if row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Scheduler job not found: {job_key}",
            )

        job = dict(row)
        db.execute(
            text(
                """
                SELECT sp_record_system_audit(
                    CAST(:actor_user_id AS uuid),
                    'SCHEDULER_JOB_RUN_REQUESTED',
                    'SCHEDULER_JOB',
                    :resource_id,
                    CAST(:new_value AS jsonb)
                )
                """
            ),
            {
                "actor_user_id": current_user["user_id"],
                "resource_id": job_key,
                "new_value": '{"source":"ADMIN_UI"}',
            },
        )
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    finally:
        db.close()

    background_tasks.add_task(
        execute_job,
        str(job["job_id"]),
        str(job["python_module"]),
    )

    return {
        "status": "ACCEPTED",
        "job_key": job_key,
        "message": "Manual execution requested",
    }


@router.get("/scheduler/jobs/{job_key}/runs")
def list_job_runs(
    job_key: str,
    limit: int = 50,
    _: CurrentUser = Depends(require_admin),
):
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT *
                FROM sp_list_scheduler_job_runs(
                    :job_key,
                    :limit
                )
                """
            ),
            {
                "job_key": job_key,
                "limit": limit,
            },
        ).mappings()
        return [dict(row) for row in rows]
    finally:
        db.close()
