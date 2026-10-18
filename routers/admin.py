from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from services.entities import RefreshMode, refresh_entities


router = APIRouter()


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
        if self.mode == RefreshMode.SYMBOL and not self.symbol:
            raise ValueError(
                "symbol è obbligatorio quando mode è SYMBOL"
            )

        if self.symbol:
            self.symbol = self.symbol.strip().upper()

        return self


@router.post("/cache/cleanup")
async def cleanup_cache(
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()

    try:
        deleted = db.execute(
            text("SELECT sp_cleanup_market_cache(30)")
        ).scalar()
        db.commit()
        return {"deleted": deleted}

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()


@router.post(
    "/entities/refresh",
    summary="Aggiorna le entities",
    description=(
        "Aggiorna una o più entities in base alla modalità richiesta."
    ),
)
def refresh_entities_endpoint(
    payload: RefreshEntitiesRequest,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()

    try:
        result = refresh_entities(
            db,
            mode=payload.mode,
            symbol=payload.symbol,
            create_if_missing=payload.create_if_missing,
            full_refresh=payload.full_refresh,
        )
        db.commit()
        return result

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
