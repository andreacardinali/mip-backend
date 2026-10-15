from fastapi import APIRouter

from sqlalchemy import text

from db import SessionLocal

from scripts.refresh_entities import (
    refresh_entities
)

router = APIRouter()


@router.post(
    "/cache/cleanup"
)
def cleanup_cache():

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
def refresh_entities_endpoint(
    payload: dict
):

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
    )
