import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_data.factory import market_data
from services.market_price_refresh import enqueue_price_refresh

router = APIRouter()


def _clean_data_source(value) -> str:
    cleaned = str(value or "YAHOO").strip().upper()
    return cleaned or "YAHOO"


def _has_price(payload) -> bool:
    return isinstance(payload, dict) and payload.get("last_price") is not None


def _enqueue_missing_price(
    db, local_payload: dict, symbol: str, data_source: str
) -> str:
    return enqueue_price_refresh(
        db,
        instrument_id=local_payload["instrument_id"],
        symbol=symbol,
        data_source=data_source,
    )


def _persist_reference_metadata(db, instrument_id, profile: dict) -> None:
    db.execute(
        text(
            """
            SELECT sp_apply_instrument_reference_metadata(
                CAST(:instrument_id AS uuid),
                CAST(:payload AS jsonb)
            )
            """
        ),
        {
            "instrument_id": instrument_id,
            "payload": json.dumps(profile, default=str),
        },
    )


@router.post("/entity")
def import_entity(payload: dict, current_user: CurrentUser = Depends(get_current_user)):
    db = SessionLocal()
    try:
        symbol = str(payload.get("symbol") or "").strip().upper()
        if not symbol:
            raise HTTPException(status_code=422, detail="symbol is required")

        requested_data_source = _clean_data_source(payload.get("data_source"))
        symbol_info = db.execute(
            text("SELECT sp_get_symbol(:symbol, 168)"), {"symbol": symbol}
        ).scalar()
        local_payload = (
            symbol_info.get("payload")
            if symbol_info and symbol_info.get("found")
            else None
        )

        if (
            symbol_info
            and symbol_info.get("source") == "LOCAL"
            and _has_price(local_payload)
        ):
            return {
                "entity_id": str(local_payload["entity_id"]),
                "instrument_id": str(local_payload["instrument_id"]),
                "imported": False,
                "source": "LOCAL",
                "price_available": True,
                "price_refresh_status": "NOT_REQUIRED",
            }

        profile = None
        if (
            symbol_info
            and symbol_info.get("source") == "CACHE"
            and _has_price(local_payload)
        ):
            profile = local_payload

        provider_data_source = _clean_data_source(
            (local_payload or {}).get("data_source") or requested_data_source
        )
        if not profile:
            profile = market_data.get_profile(
                symbol,
                data_source=provider_data_source,
                require_price=True,
                price_timeout_seconds=3,
            )

        if not profile:
            if local_payload:
                job_id = _enqueue_missing_price(
                    db, local_payload, symbol, provider_data_source
                )
                db.commit()
                return {
                    "entity_id": str(local_payload["entity_id"]),
                    "instrument_id": str(local_payload["instrument_id"]),
                    "imported": False,
                    "source": symbol_info.get("source"),
                    "price_available": False,
                    "price_refresh_status": "QUEUED",
                    "price_refresh_job_id": job_id,
                }
            raise HTTPException(status_code=404, detail="symbol not found")

        result = db.execute(
            text("SELECT * FROM sp_import_asset_profile(CAST(:payload AS jsonb))"),
            {"payload": json.dumps(profile)},
        ).first()
        if not result:
            raise RuntimeError("sp_import_asset_profile returned no row")

        _persist_reference_metadata(db, result.instrument_id, profile)

        price_available = profile.get("last_price") is not None
        refresh_status = "NOT_REQUIRED"
        refresh_job_id = None
        if not price_available:
            refresh_job_id = enqueue_price_refresh(
                db,
                instrument_id=result.instrument_id,
                symbol=symbol,
                data_source=provider_data_source,
            )
            refresh_status = "QUEUED"

        db.commit()
        return {
            "entity_id": str(result.entity_id),
            "instrument_id": str(result.instrument_id),
            "imported": result.imported,
            "source": symbol_info.get("source") if symbol_info else "PROVIDER",
            "price_available": price_available,
            "price_refresh_status": refresh_status,
            "price_refresh_job_id": refresh_job_id,
        }
    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
