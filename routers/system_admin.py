from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import text

from auth.security import CurrentUser, require_admin
from db import SessionLocal
from job_runtime.requests import REQUEST_ONLY_JOBS
from scheduler_repository import SchedulerRepository
from scheduler_triggers import SCHEDULER_TIMEZONE, build_trigger

router = APIRouter()


class JobUpdate(BaseModel):
    enabled: bool | None = None
    trigger_type: str | None = None
    cron_expression: str | None = None
    interval_minutes: int | None = None


class CronPreview(BaseModel):
    cron_expression: str


@router.post("/scheduler/cron-preview")
def preview_cron(payload: CronPreview, _: CurrentUser = Depends(require_admin)):
    try:
        trigger = build_trigger({"trigger_type": "cron", **payload.model_dump()})
        next_run = trigger.get_next_fire_time(None, datetime.now(trigger.timezone))
        return {
            "next_run_at": next_run.isoformat() if next_run else None,
            "timezone": SCHEDULER_TIMEZONE,
        }
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


@router.get("/scheduler/jobs")
def list_jobs(_: CurrentUser = Depends(require_admin)):
    db = SessionLocal()
    try:
        rows = db.execute(text("SELECT * FROM sp_list_scheduler_jobs()")).mappings()
        return [
            {**dict(row), "request_only": row["job_key"] in REQUEST_ONLY_JOBS}
            for row in rows
        ]
    finally:
        db.close()


@router.patch("/scheduler/jobs/{job_key}")
def update_job(
    job_key: str,
    payload: JobUpdate,
    current_user: CurrentUser = Depends(require_admin),
):
    if job_key in REQUEST_ONLY_JOBS and payload.enabled:
        raise HTTPException(status_code=422, detail="This job only accepts contextual execution requests")
    if payload.enabled is None and payload.trigger_type is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="No scheduler job changes were provided",
        )

    if payload.trigger_type is not None:
        try:
            build_trigger(payload.model_dump())
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error

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
        return {"status": "SUCCESS", "job_key": job_key}
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
    current_user: CurrentUser = Depends(require_admin),
):
    if job_key in REQUEST_ONLY_JOBS:
        raise HTTPException(status_code=422, detail="Start this job from its contextual API")
    db = SessionLocal()
    try:
        request = SchedulerRepository(db).request_run(
            job_key=job_key,
            requested_by=current_user["user_id"],
            payload={},
            source="ADMIN_UI",
        )
        db.commit()
        return dict(request)
    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from error
    finally:
        db.close()


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
                FROM sp_list_scheduler_job_runs(:job_key, :limit)
                """
            ),
            {"job_key": job_key, "limit": limit},
        ).mappings()
        return [dict(row) for row in rows]
    finally:
        db.close()


@router.get("/scheduler/runs/{job_run_id}/logs")
def list_run_logs(
    job_run_id: str,
    after_log_id: int = 0,
    limit: int = 500,
    _: CurrentUser = Depends(require_admin),
):
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT *
                FROM public.sp_scheduler_get_run_logs(
                    CAST(:job_run_id AS uuid),
                    :after_log_id,
                    :limit
                )
                """
            ),
            {
                "job_run_id": job_run_id,
                "after_log_id": after_log_id,
                "limit": limit,
            },
        ).mappings()
        return [dict(row) for row in rows]
    finally:
        db.close()
