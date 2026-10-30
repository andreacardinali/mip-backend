"""Disable superseded scheduler entries without deleting their history."""

import argparse
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session

from job_runtime.requests import SUPERSEDED_JOBS
from scheduler_repository import SchedulerRepository


def consolidate_jobs(db: Session, actor_id: UUID) -> list[str]:
    jobs = {
        row["job_key"]: dict(row)
        for row in db.execute(text("SELECT * FROM public.sp_list_scheduler_jobs()"))
        .mappings()
        .all()
    }
    for replacement in set(SUPERSEDED_JOBS.values()):
        if replacement not in jobs or not jobs[replacement]["enabled"]:
            raise ValueError(f"Replacement job must exist and be enabled: {replacement}")
    retiring = [jobs[key] for key in SUPERSEDED_JOBS if key in jobs]
    if any(job["last_status"] == "RUNNING" for job in retiring):
        raise ValueError("Finish superseded job runs before consolidation")
    if retiring and db.execute(
        text(
            "SELECT EXISTS ("
            "SELECT 1 FROM public.scheduler_manual_run_requests "
            "WHERE job_id = ANY(CAST(:job_ids AS uuid[])) "
            "AND status IN ('PENDING', 'PROCESSING')"
            ") OR EXISTS ("
            "SELECT 1 FROM public.scheduler_job_runs "
            "WHERE job_id = ANY(CAST(:job_ids AS uuid[])) AND status = 'RUNNING'"
            ")"
        ),
        {"job_ids": [str(job["job_id"]) for job in retiring]},
    ).scalar_one():
        raise ValueError("Resolve superseded execution requests and runs before consolidation")

    repository = SchedulerRepository(db)
    changed = []
    for job in retiring:
        if job["enabled"]:
            db.execute(
                text(
                    "SELECT * FROM public.sp_set_scheduler_job_enabled("
                    "CAST(:actor_user_id AS uuid), :job_key, :enabled)"
                ),
                {
                    "actor_user_id": str(actor_id),
                    "job_key": job["job_key"],
                    "enabled": False,
                },
            )
        if job["enabled"] or job["next_run_at"] is not None:
            repository.update_next_run(
                job_id=UUID(str(job["job_id"])), next_run_at=None
            )
            changed.append(job["job_key"])
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--actor", type=UUID, help="Existing audit user UUID")
    args = parser.parse_args()
    if not args.apply:
        print("Preview only: no database connection or changes.")
        for job_key, replacement in SUPERSEDED_JOBS.items():
            print(f"Disable {job_key}, clear next run; replacement: {replacement}")
        return
    if args.actor is None:
        parser.error("--apply requires --actor with an existing audit user UUID")

    from db import SessionLocal

    with SessionLocal() as db, db.begin():
        changed = consolidate_jobs(db, args.actor)
    print(f"Consolidation committed: {len(changed)} entries updated; history retained.")


if __name__ == "__main__":
    main()