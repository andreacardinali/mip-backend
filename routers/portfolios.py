from fastapi import APIRouter, Depends
from sqlalchemy import text

from db import SessionLocal

from auth.security import get_current_user, CurrentUser

router = APIRouter()

@router.get("/")
async def get_portfolios(
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:

        rows = db.execute(
            text(
                """
                SELECT *
                FROM sp_get_portfolios(
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
async def upsert_portfolio(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:

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
                        "USD"
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

@router.delete("/{portfolio_id}")
async def delete_portfolio(
    portfolio_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:
        deleted = db.execute(
            text(
                """
                SELECT
                sp_delete_portfolio(
                    :portfolio_id,
                    :user_id
                )
                """
            ),
            {
                "portfolio_id":
                    portfolio_id,
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

@router.get("/{portfolio_id}/summary")
async def get_portfolio_summary(
    portfolio_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:

        row = db.execute(
            text(
                """
                SELECT *
                FROM sp_get_portfolio_summary(
                    :portfolio_id,
                    :user_id
                )
                """
            ),
            {
                "portfolio_id": portfolio_id,
                "user_id": user_id,
            },
        ).first()

        return dict(
            row._mapping
        )

    finally:
        db.close()