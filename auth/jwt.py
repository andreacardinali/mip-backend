from datetime import datetime, timedelta
import jwt

from auth.config import (
    JWT_SECRET,
    JWT_ALGORITHM,
    JWT_EXPIRE_HOURS
)

def create_access_token(user_id: str):
    expire = datetime.utcnow() + timedelta(
        hours=JWT_EXPIRE_HOURS
    )

    payload = {
        "sub": user_id,
        "exp": expire
    }

    return jwt.encode(
        payload,
        JWT_SECRET,
        algorithm=JWT_ALGORITHM
    )
