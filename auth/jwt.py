from datetime import datetime, timedelta

import jwt

from config.settings import settings


def create_access_token(user_id: str):
    expire = datetime.utcnow() + timedelta(hours=int(settings.JWT_EXPIRE_HOURS))

    payload = {"sub": user_id, "exp": expire}

    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)
