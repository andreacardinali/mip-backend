from fastapi import APIRouter
from sqlalchemy import text

from db import SessionLocal

router = APIRouter()


@router.get(
    "/portfolio/{portfolio_id}"
)
def get_positions(
    portfolio_id: str
):

    db = SessionLocal()

    try:

        rows = db.execute(
            text(
                """
                SELECT *
                FROM sp_get_positions(
                    :portfolio_id
                )
                """
            ),
            {
                "portfolio_id":
                    portfolio_id
            },
        )

        return [
            dict(row._mapping)
            for row in rows
        ]

    finally:

        db.close()


@router.post("/upsert")
def upsert_position(
    payload: dict
):

    if "instrument_id" not in payload:
        return {
            "error":
                "missing instrument_id",
            "payload":
                payload
        }

    db = SessionLocal()


    try:

        position_id = db.execute(
            text(
                """
                SELECT sp_upsert_position(
                    :position_id,
                    :portfolio_id,
                    :instrument_id,
                    :quantity,
                    :avg_price,
                    :opened_at,
                    :notes
                )
                """
            ),
            {
                "position_id":
                    payload.get(
                        "position_id"
                    ),

                "portfolio_id":
                    payload[
                        "portfolio_id"
                    ],

                "instrument_id":
                    payload[
                        "instrument_id"
                    ],

                "quantity":
                    payload[
                        "quantity"
                    ],

                "avg_price":
                    payload.get(
                        "avg_price"
                    ),

                "opened_at":
                    payload.get(
                        "opened_at"
                    ),

                "notes":
                    payload.get(
                        "notes"
                    ),
            },
        ).scalar()

        db.commit()

        return {
            "position_id":
                str(position_id),
            "status":
                "saved",
        }

    finally:

        db.close()


@router.delete(
    "/{position_id}"
)
def delete_position(
    position_id: str
):

    db = SessionLocal()

    try:
        deleted = db.execute(
            text(
                """
                SELECT
                sp_delete_position(
                    :position_id
                )
                """
            ),
            {
                "position_id":
                    position_id
            },
        ).scalar()

        db.commit()

        return {
            "deleted":
                deleted
        }

    finally:

        db.close()