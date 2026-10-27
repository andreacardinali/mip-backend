from typing import TypedDict

import jwt
from fastapi import Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import text

from config.settings import settings
from db import SessionLocal


class CurrentUser(TypedDict):
    user_id: str
    username: str
    email: str
    display_name: str | None
    is_active: bool
    reporting_currency: str
    roles: list[str]


oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")


def get_current_user(token: str = Depends(oauth2_scheme)) -> CurrentUser:
    try:
        uid = jwt.decode(
            token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM]
        ).get("sub")
        if not uid:
            raise ValueError()
    except Exception:
        raise HTTPException(401, "Invalid token") from None
    db = SessionLocal()
    try:
        row = (
            db.execute(
                text("SELECT * FROM sp_get_user_context(CAST(:id AS uuid))"),
                {"id": uid},
            )
            .mappings()
            .one_or_none()
        )
        if not row:
            raise HTTPException(401, "User not found")
        if not row["is_active"]:
            raise HTTPException(403, "User inactive")
        x = dict(row)
        x["user_id"] = str(x["user_id"])
        x["roles"] = list(x["roles"] or [])
        return x
    finally:
        db.close()


async def require_admin(user: CurrentUser = Depends(get_current_user)) -> CurrentUser:
    if "ADMIN" not in user["roles"]:
        raise HTTPException(403, "Administrator role required")
    return user
