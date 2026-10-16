from fastapi import APIRouter, Depends
from sqlalchemy import text

from db import SessionLocal

from auth.security import get_current_user, CurrentUser

from scripts.refresh_entities import (
    refresh_entities
)

router = APIRouter()


@router.post(
    "/cache/cleanup"
)
async def cleanup_cache(
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:

        deleted = db.execute(
            text(
                """
                SELECT
                sp_cleanup_market_cache(
                    30
                )
                """
            )
        ).scalar()

        db.commit()

        return {
            "deleted":
                deleted
        }

    finally:

        db.close()

@router.post("/entities/refresh")
async def refresh_entities_endpoint(
    payload: dict,
):

    current_user: CurrentUser = Depends(get_current_user),
    user_id = current_user["user_id"]
    db = SessionLocal()

    print(payload)

    return refresh_entities(
        mode=payload.get(
            "mode",
            "MISSING"
        ),
        symbol=payload.get(
            "symbol"
        ),
        create_if_missing=payload.get(
            "create_if_missing",
            False
        ),
        full_refresh=payload.get(
            "full_refresh",
            False
        ),
    )
