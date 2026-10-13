from uuid import uuid4

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
                SELECT
                    watchlist_id,
                    user_id,
                    name,
                    description,
                    created_at,
                    updated_at
                FROM watchlists
                ORDER BY name
                """
            )
        )

        return [
            dict(row._mapping)
            for row in rows
        ]

    finally:

        db.close()

@router.post("")
def create_watchlist(payload: dict):

    db = SessionLocal()

    try:

        # USER DI TEST
        user_id = "5fc35381-dfbf-4d67-8c4e-479bb279542b"

        watchlist_id = str(uuid4())

        db.execute(
            text(
                """
                INSERT INTO watchlists
                (
                    watchlist_id,
                    user_id,
                    name,
                    description,
                    created_at,
                    updated_at
                )
                VALUES
                (
                    :watchlist_id,
                    :user_id,
                    :name,
                    :description,
                    NOW(),
                    NOW()
                )
                """
            ),
            {
                "watchlist_id": watchlist_id,
                "user_id": user_id,
                "name": payload["name"],
                "description": payload.get("description"),
            },
        )

        db.commit()

        return {
            "watchlist_id": watchlist_id,
            "status": "created",
        }

    finally:

        db.close()

@router.delete("/{watchlist_id}")
def delete_watchlist(watchlist_id: str):

    db = SessionLocal()

    try:

        result = db.execute(
            text(
                """
                DELETE FROM watchlists
                WHERE watchlist_id = :watchlist_id
                """
            ),
            {
                "watchlist_id": watchlist_id
            }
        )

        db.commit()

        return {
            "deleted": result.rowcount > 0
        }

    finally:

        db.close()