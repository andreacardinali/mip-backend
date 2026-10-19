from typing import TypedDict

import jwt
from fastapi import (
    Depends,
    HTTPException,
)
from fastapi.security import (
    OAuth2PasswordBearer,
)
from sqlalchemy import text

from config.settings import settings
from db import SessionLocal


class CurrentUser(TypedDict):
    user_id: str
    username: str
    email: str
    is_active: bool

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


async def get_current_user(token: str = Depends(oauth2_scheme)) -> CurrentUser:

    try:

        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )

        user_id = payload.get("sub")

        if not user_id:

            raise HTTPException(
                status_code=401,
                detail="Invalid token",
            )

    except Exception:

        raise HTTPException(
            status_code=401,
            detail="Invalid token",
        ) from None

    db = SessionLocal()

    try:

        row = db.execute(
            text(
                """
                SELECT
                    user_id,
                    username,
                    email,
                    is_active
                FROM users
                WHERE user_id = :user_id
                """
            ),
            {"user_id": user_id},
        ).first()

        if not row:
            raise HTTPException(
                status_code=401,
                detail="User not found",
            )

        if not row.is_active:
            raise HTTPException(
                status_code=403,
                detail="User is inactive",
            )

        return dict(row._mapping)

    finally:

        db.close()
