from fastapi import APIRouter, Depends
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal

router = APIRouter()


@router.get("")
async def get_entities(
    current_user: CurrentUser = Depends(get_current_user),
):
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

        return [dict(row._mapping) for row in rows]

    finally:
        db.close()
