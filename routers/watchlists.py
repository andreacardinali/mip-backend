from fastapi import APIRouter
from sqlalchemy import text

from db import SessionLocal

router = APIRouter()


@router.get("")
def get_watchlists():

    db = SessionLocal()

    try:

        rows = db.execute(
            text(
                """
                SELECT *
                FROM sp_get_watchlists()
                """
            )
        )

        return [
            dict(row._mapping)
            for row in rows
        ]

    finally:

        db.close()


@router.post("/upsert")
def upsert_watchlist(
    payload: dict
):

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
def delete_watchlist(
    watchlist_id: str
):

    db = SessionLocal()

    try:
        deleted = db.execute(
            text(
                """
                SELECT
                sp_delete_watchlist(
                    :watchlist_id
                )
                """
            ),
            {
                "watchlist_id":
                    watchlist_id
            },
        ).scalar()

        db.commit()

        return {
            "deleted":
                deleted
        }

    finally:

        db.close()