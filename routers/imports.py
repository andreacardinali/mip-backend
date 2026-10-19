import json

from fastapi import APIRouter, Depends
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from providers.manager import market_data

router = APIRouter()


@router.post("/entity")
def import_entity(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()

    try:
        symbol = payload["symbol"].strip().upper()

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

        #
        # LOCAL
        #

        if (
            symbol_info
            and symbol_info.get("found")
            and symbol_info.get("source") == "LOCAL"
        ):

            local_payload = symbol_info["payload"]

            return {
                "entity_id": str(local_payload["entity_id"]),
                "instrument_id": str(local_payload["instrument_id"]),
                "imported": False,
                "source": "LOCAL",
            }

        profile = None

        #
        # CACHE
        #

        if (
            symbol_info
            and symbol_info.get("found")
            and symbol_info.get("source") == "CACHE"
        ):

            profile = symbol_info["payload"]

        #
        # PROVIDER
        #

        if not profile:

            profile = market_data.get_profile(symbol)

            if not profile:

                return {"error": "symbol not found"}

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
        }

    except Exception:
        db.rollback()
        raise

    finally:

        db.close()
