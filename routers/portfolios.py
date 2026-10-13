from uuid import uuid4

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
                SELECT
                    portfolio_id,
                    user_id,
                    name,
                    portfolio_type,
                    description,
                    base_currency,
                    created_at,
                    updated_at
                FROM portfolios
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
def create_portfolio(payload: dict):

    db = SessionLocal()

    try:
        # USER DI TEST
        user_id = "5fc35381-dfbf-4d67-8c4e-479bb279542b"

        portfolio_id = str(uuid4())

        db.execute(
            text(
                """
                INSERT INTO portfolios
                (
                    portfolio_id,
                    user_id,
                    name,
                    portfolio_type,
                    description,
                    base_currency,
                    created_at,
                    updated_at
                )
                VALUES
                (
                    :portfolio_id,
                    :user_id,
                    :name,
                    :portfolio_type,
                    :description,
                    :base_currency,
                    NOW(),
                    NOW()
                )
                """
            ),
            {
                "portfolio_id": portfolio_id,
                "user_id": user_id,
                "name": payload["name"],
                "portfolio_type": payload.get(
                    "portfolioType",
                    "PORTFOLIO"
                ),
                "description": payload.get(
                    "description"
                ),
                "base_currency": payload.get(
                    "baseCurrency",
                    "CHF"
                ),
            },
        )

        db.commit()

        return {
            "portfolio_id": portfolio_id,
            "status": "created",
        }

    finally:

        db.close()