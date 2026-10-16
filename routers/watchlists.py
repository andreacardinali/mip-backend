from fastapi import APIRouter, Depends
from sqlalchemy import text

from db import SessionLocal

from auth.security import get_current_user, CurrentUser

router = APIRouter()

@router.get("")
async def get_watchlists(
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:

        rows = db.execute(
            text(
                """
                SELECT *
                FROM sp_get_watchlists(
                    :user_id
                )
                """
            ),
            {
                "user_id":
                    user_id,
            }
        )

        return [
            dict(row._mapping)
            for row in rows
        ]

    finally:

        db.close()


@router.post("/upsert")
async def upsert_watchlist(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:

        user_id = (
            "5fc35381-dfbf-4d67-8c4e-479bb279542b"
        )

        watchlist_id = db.execute(
            text(
                """
                SELECT sp_upsert_watchlist(
                    :watchlist_id,
                    :user_id,
                    :name,
                    :description
                )
                """
            ),
            {
                "watchlist_id":
                    payload.get(
                        "watchlist_id"
                    ),
                "user_id":
                    user_id,
                "name":
                    payload["name"],
                "description":
                    payload.get(
                        "description"
                    ),
            }
        ).scalar()

        db.commit()

        return {
            "watchlist_id":
                str(watchlist_id),
            "status":
                "saved",
        }

    finally:

        db.close()


@router.delete(
    "/{watchlist_id}"
)
async def delete_watchlist(
    watchlist_id: str,
    current_user: CurrentUser = Depends(get_current_user),    
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:
        deleted = db.execute(
            text(
                """
                SELECT
                sp_delete_watchlist(
                    :watchlist_id,
                    :user_id
                )
                """
            ),
            {
                "watchlist_id":
                    watchlist_id,
                "user_id":
                    user_id,
            },
        ).scalar()

        db.commit()

        return {
            "deleted":
                deleted
        }

    finally:

        db.close()