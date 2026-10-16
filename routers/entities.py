from fastapi import APIRouter, Depends
from sqlalchemy import text

from db import SessionLocal

from auth.security import get_current_user, CurrentUser

router = APIRouter()

@router.get("")
async def get_entities(
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:

        rows = db.execute(
            text(
                """
                SELECT *
                FROM sp_get_entities(
                    500
                )
                """
            )
        )

        return [
            dict(row._mapping)
            for row in rows
        ]

    finally:

        db.close()
