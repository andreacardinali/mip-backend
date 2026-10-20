from fastapi import APIRouter, Depends, HTTPException
from pwdlib import PasswordHash
from sqlalchemy import text

from auth.jwt import create_access_token
from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_data.factory import market_data
from services.currencies import normalize_supported_currency

router = APIRouter()
password_hash = PasswordHash.recommended()


@router.post("/login")
def login(payload: dict):
    db = SessionLocal()
    try:
        row = db.execute(
            text("SELECT * FROM sp_get_user_for_login(:username)"),
            {"username": payload["username"]},
        ).first()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid credentials")

        user = row._mapping
        if not user["is_active"]:
            raise HTTPException(status_code=403, detail="User disabled")
        if not password_hash.verify(payload["password"], user["password_hash"]):
            raise HTTPException(status_code=401, detail="Invalid credentials")

        token = create_access_token(str(user["user_id"]))
        return {
            "access_token": token,
            "token_type": "bearer",
            "user_id": str(user["user_id"]),
            "username": user["username"],
        }
    finally:
        db.close()


@router.get("/me")
async def me(current_user: CurrentUser = Depends(get_current_user)):
    return current_user


@router.put("/me/preferences")
async def update_preferences(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    try:
        reporting_currency = normalize_supported_currency(
            payload.get("reporting_currency"),
            market_data,
        )
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=503,
            detail="Currency catalog is temporarily unavailable",
        ) from error

    db = SessionLocal()
    try:
        row = db.execute(
            text(
                """
                UPDATE users
                SET reporting_currency = :reporting_currency,
                    updated_at = now()
                WHERE user_id = :user_id
                RETURNING
                    user_id,
                    username,
                    email,
                    is_active,
                    reporting_currency
                """
            ),
            {
                "user_id": current_user["user_id"],
                "reporting_currency": reporting_currency,
            },
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="User not found")
        db.commit()
        return dict(row._mapping)
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
