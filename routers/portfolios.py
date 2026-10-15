from fastapi import APIRouter
from sqlalchemy import text

from db import SessionLocal

router = APIRouter()


@router.get("")
def get_portfolios():

    db = SessionLocal()

    try:

        rows = db.execute(
            text(
                """
                SELECT *
                FROM sp_get_portfolios()
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
def upsert_portfolio(
    payload: dict
):

    db = SessionLocal()

    try:

        user_id = (
            "5fc35381-dfbf-4d67-8c4e-479bb279542b"
        )

        portfolio_id = db.execute(
            text(
                """
                SELECT sp_upsert_portfolio(
                    :portfolio_id,
                    :user_id,
                    :name,
                    :portfolio_type,
                    :description,
                    :base_currency
                )
                """
            ),
            {
                "portfolio_id":
                    payload.get(
                        "portfolio_id"
                    ),

                "user_id":
                    user_id,

                "name":
                    payload["name"],

                "portfolio_type":
                    payload.get(
                        "portfolio_type",
                        "PORTFOLIO"
                    ),

                "description":
                    payload.get(
                        "description"
                    ),

                "base_currency":
                    payload.get(
                        "base_currency",
                        "CHF"
                    ),
            },
        ).scalar()

        db.commit()

        return {
            "portfolio_id":
                str(portfolio_id),
            "status":
                "saved",
        }

    finally:

        db.close()


@router.delete(
    "/{portfolio_id}"
)
def delete_portfolio(
    portfolio_id: str
):

    db = SessionLocal()

    try:
        deleted = db.execute(
            text(
                """
                SELECT
                sp_delete_portfolio(
                    :portfolio_id
                )
                """
            ),
            {
                "portfolio_id":
                    portfolio_id
            },
        ).scalar()

        db.commit()

        return {
            "deleted":
                deleted
        }


    finally:

        db.close()