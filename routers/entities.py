from fastapi import APIRouter
from sqlalchemy import text

from db import SessionLocal

router = APIRouter()

@router.get("")
def get_entities():

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
