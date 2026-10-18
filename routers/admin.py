from enum import Enum
from fastapi import APIRouter, Depends
from sqlalchemy import text
from pydantic import BaseModel, Field, model_validator
from db import SessionLocal

from auth.security import get_current_user, CurrentUser

from scripts.refresh_entities import (
    refresh_entities
)

router = APIRouter()

class RefreshMode(str, Enum):
    MISSING = "MISSING"
    SYMBOL = "SYMBOL"
    ALL = "ALL"


class RefreshEntitiesRequest(BaseModel):
    mode: RefreshMode = Field(
        default=RefreshMode.MISSING,
        description="Modalità di aggiornamento delle entities",
    )

    symbol: str | None = Field(
        default=None,
        description="Simbolo da aggiornare quando mode è SYMBOL",
        examples=["AAPL"],
    )

    create_if_missing: bool = Field(
        default=False,
        description="Crea l'entity se non esiste",
    )

    full_refresh: bool = Field(
        default=False,
        description="Forza l'aggiornamento completo",
    )

    @model_validator(mode="after")
    def validate_request(self):
        if self.mode == "SYMBOL" and not self.symbol:
            raise ValueError(
                "symbol è obbligatorio quando mode è SYMBOL"
            )

        if self.symbol:
            self.symbol = self.symbol.strip().upper()

        return self


@router.post(
    "/cache/cleanup"
)
async def cleanup_cache(
    current_user: CurrentUser = Depends(get_current_user),
):
    user_id = current_user["user_id"]
    db = SessionLocal()

    try:

        deleted = db.execute(
            text(
                """
                SELECT
                sp_cleanup_market_cache(
                    30
                )
                """
            )
        ).scalar()

        db.commit()

        return {
            "deleted":
                deleted
        }

    finally:

        db.close()

@router.post(
    "/entities/refresh",
    summary="Aggiorna le entities",
    description="Aggiorna una o più entities in base alla modalità richiesta.",
)
def refresh_entities_endpoint(
    payload: RefreshEntitiesRequest,
):
    return refresh_entities(
        mode=payload.mode,
        symbol=payload.symbol,
        create_if_missing=payload.create_if_missing,
        full_refresh=payload.full_refresh,
    )