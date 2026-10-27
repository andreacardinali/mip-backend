from decimal import Decimal, InvalidOperation

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_data.factory import market_data
from services.currencies import normalize_supported_currency

router = APIRouter()
PRICE_MAX_AGE_HOURS = 96


@router.get("/portfolio/{portfolio_id}")
def get_positions(
    portfolio_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT public.sp_get_positions_with_pricing(
                    CAST(:portfolio_id AS uuid),
                    CAST(:user_id AS uuid),
                    CAST(:max_age_hours AS numeric)
                ) AS payload
                """
            ),
            {
                "portfolio_id": portfolio_id,
                "user_id": current_user["user_id"],
                "max_age_hours": PRICE_MAX_AGE_HOURS,
            },
        )
        return [row.payload for row in rows]
    finally:
        db.close()


@router.post("/upsert")
def upsert_position(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    if "instrument_id" not in payload:
        raise HTTPException(status_code=422, detail="instrument_id is required")

    cost_amount = payload.get("cost_basis_amount")
    cost_currency = None
    if cost_amount is not None:
        try:
            cost_amount = Decimal(str(cost_amount))
            if not cost_amount.is_finite() or cost_amount < 0:
                raise ValueError("cost_basis_amount must be finite and non-negative")
            cost_currency = normalize_supported_currency(
                payload.get("cost_basis_currency"), market_data
            )
        except (ValueError, InvalidOperation) as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=503, detail="Currency catalog is temporarily unavailable"
            ) from error

    db = SessionLocal()
    try:
        position_id = db.execute(
            text(
                """
                SELECT sp_upsert_position(
                    CAST(:position_id AS uuid),
                    CAST(:portfolio_id AS uuid),
                    CAST(:user_id AS uuid),
                    CAST(:instrument_id AS uuid),
                    CAST(:quantity AS numeric),
                    CAST(:avg_price AS numeric),
                    CAST(:opened_at AS timestamptz),
                    CAST(:notes AS text)
                )
                """
            ),
            {
                "position_id": payload.get("position_id"),
                "portfolio_id": payload["portfolio_id"],
                "user_id": user_id,
                "instrument_id": payload["instrument_id"],
                "quantity": payload["quantity"],
                "avg_price": payload.get("avg_price"),
                "opened_at": payload.get("opened_at"),
                "notes": payload.get("notes"),
            },
        ).scalar()
        if "cost_basis_amount" in payload:
            updated_position_id = db.execute(
                text(
                    """
                    SELECT public.sp_set_position_cost_basis(
                        CAST(:position_id AS uuid), CAST(:user_id AS uuid),
                        CAST(:amount AS numeric), CAST(:currency AS character(3))
                    )
                    """
                ),
                {
                    "position_id": str(position_id),
                    "user_id": user_id,
                    "amount": cost_amount,
                    "currency": cost_currency,
                },
            ).scalar()
            if updated_position_id is None:
                raise HTTPException(status_code=404, detail="Position not found")
        db.commit()
        return {"position_id": str(position_id), "status": "saved"}
    except IntegrityError as error:
        db.rollback()
        diagnostic = getattr(error.orig, "diag", None)
        constraint_name = getattr(diagnostic, "constraint_name", None)
        if constraint_name == "uq_position":
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "POSITION_ALREADY_EXISTS",
                    "message": "This instrument already exists in the portfolio.",
                },
            ) from error
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@router.delete("/{position_id}")
def delete_position(
    position_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        deleted = db.execute(
            text("SELECT sp_delete_position(:position_id, :user_id)"),
            {"position_id": position_id, "user_id": current_user["user_id"]},
        ).scalar()
        db.commit()
        return {"deleted": deleted}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
