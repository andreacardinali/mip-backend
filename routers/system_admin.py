from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from pydantic import BaseModel, ValidationError
from sqlalchemy import text

from auth.security import CurrentUser, require_admin
from db import SessionLocal
from job_runtime.requests import REQUEST_ONLY_JOBS, SUPERSEDED_JOBS
from scheduler_repository import SchedulerRepository
from scheduler_triggers import SCHEDULER_TIMEZONE, build_trigger
from services.entity_refresh_request import RefreshEntitiesRequest
from services.market_screener_pipeline_request import MarketScreenerPipelineRequest

router = APIRouter()


class JobUpdate(BaseModel):
    enabled: bool | None = None
    trigger_type: str | None = None
    cron_expression: str | None = None
    interval_minutes: int | None = None


class CronPreview(BaseModel):
    cron_expression: str


@router.get("/diagnostics")
def get_pipeline_diagnostics(
    limit: int = Query(default=50, ge=1, le=200),
    _: CurrentUser = Depends(require_admin),
):
    db = SessionLocal()
    try:

        def rows(statement: str, parameters: dict[str, Any] | None = None):
            result = db.execute(text(statement), parameters or {})
            return [dict(row) for row in result.mappings().all()]

        return {
            "generated_at": datetime.now(UTC),
            "window_days": 7,
            "ingestion": {
                "by_stage_source": rows(
                    """
                    SELECT stage, source, COUNT(*)::integer AS event_count,
                           MAX(ingested_at) AS latest_ingested_at
                    FROM public.event_lake
                    WHERE ingested_at >= now() - interval '7 days'
                    GROUP BY stage, source
                    ORDER BY latest_ingested_at DESC, stage, source
                    """
                ),
                "recent_events": rows(
                    """
                    SELECT event_id, event_type, stage, source, event_key,
                           revision, entity_id, occurred_at, ingested_at
                    FROM public.event_lake
                    ORDER BY ingested_at DESC
                    LIMIT :limit
                    """,
                    {"limit": limit},
                ),
            },
            "processing": {
                "by_status": rows(
                    """
                    SELECT UPPER(status) AS status, COUNT(*)::integer AS run_count,
                           MAX(started_at) AS latest_started_at
                    FROM public.scheduler_job_runs
                    WHERE started_at >= now() - interval '7 days'
                    GROUP BY UPPER(status)
                    ORDER BY run_count DESC, status
                    """
                ),
                "recent_runs": rows(
                    """
                    SELECT r.job_run_id, j.job_key, j.job_name, r.status,
                           r.started_at, r.completed_at, r.error_message, r.result
                    FROM public.scheduler_job_runs r
                    JOIN public.scheduler_jobs j ON j.job_id = r.job_id
                    ORDER BY r.started_at DESC
                    LIMIT :limit
                    """,
                    {"limit": limit},
                ),
            },
            "notifications": {
                "by_status_channel": rows(
                    """
                    SELECT UPPER(status) AS status,
                           UPPER(COALESCE(channel, 'N/D')) AS channel,
                           COUNT(*)::integer AS notification_count,
                           MAX(created_at) AS latest_created_at
                    FROM public.notifications
                    WHERE created_at >= now() - interval '7 days'
                    GROUP BY UPPER(status), UPPER(COALESCE(channel, 'N/D'))
                    ORDER BY notification_count DESC, status, channel
                    """
                ),
                "recent": rows(
                    """
                    SELECT notification_id, notification_type, title, status, channel,
                           severity, attempt_count, next_attempt_at, last_error,
                           created_at, sent_at
                    FROM public.notifications
                    ORDER BY created_at DESC
                    LIMIT :limit
                    """,
                    {"limit": limit},
                ),
                "channels": rows(
                    """
                    SELECT UPPER(source_type) AS channel,
                           COUNT(*)::integer AS configured_sources,
                           COUNT(*) FILTER (
                               WHERE COALESCE((config->>'enabled')::boolean, true)
                           )::integer AS enabled_sources,
                           COUNT(*) FILTER (
                               WHERE COALESCE(
                                   NULLIF(config->>'credential_encrypted', ''),
                                   NULLIF(config->>'bot_token', ''),
                                   NULLIF(config->>'token', '')
                               ) IS NOT NULL
                           )::integer AS credentialed_sources,
                           COUNT(*) FILTER (
                               WHERE COALESCE((config->>'enabled')::boolean, true)
                                 AND COALESCE(
                                     NULLIF(config->>'credential_encrypted', ''),
                                     NULLIF(config->>'bot_token', ''),
                                     NULLIF(config->>'token', '')
                                 ) IS NOT NULL
                           )::integer AS ready_sources
                    FROM public.user_sources
                    GROUP BY UPPER(source_type)
                    ORDER BY channel
                    """
                ),
            },
        }
    finally:
        db.close()


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
            {
                **dict(row),
                "request_only": row["job_key"] in REQUEST_ONLY_JOBS,
            }
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
    if job_key in SUPERSEDED_JOBS and (
        payload.enabled or payload.trigger_type is not None
    ):
        raise HTTPException(
            status_code=422, detail=f"Use {SUPERSEDED_JOBS[job_key]} instead"
        )
    if job_key in REQUEST_ONLY_JOBS and payload.enabled:
        raise HTTPException(
            status_code=422,
            detail="This job only accepts contextual execution requests",
        )
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
    payload: Annotated[dict[str, Any] | None, Body()] = None,
):
    if job_key in SUPERSEDED_JOBS:
        raise HTTPException(
            status_code=422, detail=f"Use {SUPERSEDED_JOBS[job_key]} instead"
        )
    if job_key in REQUEST_ONLY_JOBS:
        raise HTTPException(
            status_code=422, detail="Start this job from its contextual API"
        )
    parameters = payload or {}
    model = {
        "market_screener_pipeline": MarketScreenerPipelineRequest,
        "refresh_entities_missing": RefreshEntitiesRequest,
    }.get(job_key)
    try:
        if model is not None:
            parameters = model.model_validate(parameters).model_dump(
                mode="json", exclude_unset=True
            )
        elif parameters:
            raise ValueError("This job does not accept execution parameters")
    except (ValidationError, ValueError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    db = SessionLocal()
    try:
        request = SchedulerRepository(db).request_run(
            job_key=job_key,
            requested_by=current_user["user_id"],
            payload=parameters,
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
