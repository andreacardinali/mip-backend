from fastapi import APIRouter, Depends
from sqlalchemy import text

from db import SessionLocal

from auth.security import get_current_user, CurrentUser

router = APIRouter()


@router.get(
    "/{watchlist_id}/items"
)
async def get_watchlist_items(
    watchlist_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:

        rows = db.execute(
            text(
                """
                SELECT *
                FROM sp_get_watchlist_items(
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
        )

        return [
            dict(row._mapping)
            for row in rows
        ]

    finally:

        db.close()


@router.post("/upsert")
async def upsert_watchlist_item(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:

        item_id = db.execute(
            text(
                """
                SELECT
                sp_upsert_watchlist_item(
                    :watchlist_item_id,
                    :watchlist_id,
                    :user_id,
                    :entity_id,
                    :priority,
                    :target_price,
                    :notes
                )
                """
            ),
            {
                "watchlist_item_id":
                    payload.get(
                        "watchlist_item_id"
                    ),

                "watchlist_id":
                    payload[
                        "watchlist_id"
                    ],

                "user_id":
                    user_id,

                "entity_id":
                    payload[
                        "entity_id"
                    ],
                "priority":
                    payload.get(
                        "priority"
                    ),
                "target_price":
                    payload.get(
                        "target_price"
                    ),
                "notes":
                    payload.get(
                        "notes"
                    ),
            },
        ).scalar()

        db.commit()

        return {
            "watchlist_item_id":
                str(item_id),
            "status":
                "saved",
        }

    finally:

        db.close()


@router.delete(
    "/{watchlist_item_id}"
)
async def delete_watchlist_item(
    watchlist_item_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:
        deleted = db.execute(
            text(
                """
                SELECT
                sp_delete_watchlist_item(
                    :watchlist_item_id,
                    :user_id
                )
                """
            ),
            {
                "watchlist_item_id":
                    watchlist_item_id,
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