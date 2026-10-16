from datetime import datetime

import jwt
from fastapi import (
    Depends,
    HTTPException,
)
from fastapi.security import (
    OAuth2PasswordBearer,
)

from sqlalchemy import text

from db import SessionLocal

from auth.config import (
    JWT_SECRET,
    JWT_ALGORITHM,
    JWT_EXPIRE_HOURS
)

from typing import TypedDict

class CurrentUser(TypedDict):
    user_id: str
    username: str
    email: str

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/auth/login"
)


async def get_current_user(
    token: str = Depends(oauth2_scheme)
) -> CurrentUser:

    try:

        payload = jwt.decode(
            token,
            JWT_SECRET,
            algorithms=[JWT_ALGORITHM],
        )

        user_id = payload.get(
            "sub"
        )

        if not user_id:

            raise HTTPException(
                status_code=401,
                detail="Invalid token",
            )

    except Exception:

        raise HTTPException(
            status_code=401,
            detail="Invalid token",
        )

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
            {
                "user_id": user_id
            },
        ).first()

        if not row:

            raise HTTPException(
                status_code=401,
                detail="User not found",
            )

        return dict(
            row._mapping
        )

    finally:

        db.close()
