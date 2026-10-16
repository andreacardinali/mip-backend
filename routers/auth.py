from fastapi import (
    APIRouter,
    HTTPException,
    Depends,
)

from sqlalchemy import text

from pwdlib import PasswordHash

from db import SessionLocal

from auth.jwt import (
    create_access_token,
)

from auth.security import get_current_user, CurrentUser

router = APIRouter()

password_hash = PasswordHash.recommended()

@router.post("/login")
def login(payload: dict):

    db = SessionLocal()

    try:

        row = db.execute(
            text(
                """
                SELECT *
                FROM sp_get_user_for_login(
                    :username
                )
                """
            ),
            {
                "username":
                    payload["username"]
            },
        ).first()

        if not row:

            raise HTTPException(
                status_code=401,
                detail="Invalid credentials"
            )

        user = row._mapping

        if not user["is_active"]:

            raise HTTPException(
                status_code=403,
                detail="User disabled"
            )

        if not password_hash.verify(
            payload["password"],
            user["password_hash"]
        ):

            raise HTTPException(
                status_code=401,
                detail="Invalid credentials"
            )

        token = create_access_token(
            str(
                user["user_id"]
            )
        )

        return {
            "access_token":
                token,

            "token_type":
                "bearer",

            "user_id":
                str(
                    user["user_id"]
                ),

            "username":
                user["username"],
        }

    finally:

        db.close()

@router.get("/me")
async def me(
    current_user = Depends(get_current_user)
):
    return current_user
