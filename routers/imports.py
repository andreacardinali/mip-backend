import json

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from providers.manager import market_data

router = APIRouter()


def _clean_data_source(value) -> str:
    cleaned = str(value or "YAHOO").strip().upper()
    return cleaned or "YAHOO"


def _has_price(payload) -> bool:
    return isinstance(payload, dict) and payload.get("last_price") is not None


@router.post("/entity")
def import_entity(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()

    try:
        symbol = str(payload.get("symbol") or "").strip().upper()

        if not symbol:
            raise HTTPException(status_code=422, detail="symbol is required")

        requested_data_source = _clean_data_source(payload.get("data_source"))

        symbol_info = db.execute(
            text(
                """
                SELECT
                sp_get_symbol(
                    :symbol,
                    168
                )
                """
            ),
            {"symbol": symbol},
        ).scalar()

        local_payload = None

        if symbol_info and symbol_info.get("found"):
            local_payload = symbol_info.get("payload")

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
            }

        profile = None

        if (
            symbol_info
            and symbol_info.get("source") == "CACHE"
            and _has_price(local_payload)
        ):
            profile = local_payload

        if not profile:
            provider_data_source = _clean_data_source(
                (local_payload or {}).get("data_source") or requested_data_source
            )

            profile = market_data.get_profile(
                symbol,
                data_source=provider_data_source,
                require_price=True,
            )

        if not profile:
            if local_payload:
                return {
                    "entity_id": str(local_payload["entity_id"]),
                    "instrument_id": str(local_payload["instrument_id"]),
                    "imported": False,
                    "source": symbol_info.get("source"),
                    "price_available": _has_price(local_payload),
                }

            raise HTTPException(status_code=404, detail="symbol not found")

        result = db.execute(
            text(
                """
                SELECT *
                FROM
                sp_import_asset_profile(
                    CAST(
                        :payload
                        AS jsonb
                    )
                )
                """
            ),
            {"payload": json.dumps(profile)},
        ).first()

        db.commit()

        return {
            "entity_id": str(result.entity_id),
            "instrument_id": str(result.instrument_id),
            "imported": result.imported,
            "source": (symbol_info.get("source") if symbol_info else "PROVIDER"),
            "price_available": profile.get("last_price") is not None,
        }

    except HTTPException:
        db.rollback()
        raise

    except Exception:
        db.rollback()
        raise

    finally:
        db.close()
