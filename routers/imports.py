from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from market_data.capabilities import InstrumentType
from market_data.factory import market_data
from market_data.models import InstrumentReference
from services.market_price_refresh import enqueue_price_refresh
from services.profile_persistence import pin_profile_to_listing, upsert_asset_profile

router = APIRouter()


def _clean_data_source(value) -> str:
    cleaned = str(value or "YAHOO").strip().upper()
    return cleaned or "YAHOO"


def _clean_optional(value):
    text_value = str(value or "").strip()
    return text_value or None


def _instrument_type(value) -> InstrumentType:
    normalized = str(value or "").strip().upper()
    try:
        return InstrumentType(normalized)
    except ValueError:
        return InstrumentType.UNKNOWN


def _has_price(payload) -> bool:
    return isinstance(payload, dict) and payload.get("last_price") is not None


def _matches_listing_hints(payload, hints: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    aliases = {
        "exchange": "exchange_code",
        "currency": "currency_code",
        "instrument_type": "type",
    }
    for key, requested in hints.items():
        if not requested:
            continue
        actual = payload.get(key) or payload.get(aliases.get(key, key))
        if str(actual or "").strip().upper() != str(requested).strip().upper():
            return False
    return True


def _has_persisted_identity(payload) -> bool:
    return (
        isinstance(payload, dict)
        and payload.get("entity_id") is not None
        and payload.get("instrument_id") is not None
    )


def _has_currency(payload) -> bool:
    return (
        isinstance(payload, dict)
        and _clean_optional(payload.get("currency") or payload.get("currency_code"))
        is not None
    )


def _enqueue_missing_price(db, local_payload, symbol, data_source):
    if not _has_persisted_identity(local_payload):
        return None

    return enqueue_price_refresh(
        db,
        instrument_id=local_payload["instrument_id"],
        symbol=symbol,
        data_source=data_source,
    )


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
        requested_isin = _clean_optional(payload.get("isin"))
        requested_currency = _clean_optional(payload.get("currency"))
        requested_exchange = _clean_optional(
            payload.get("exchange_code") or payload.get("exchange")
        )
        requested_type = _instrument_type(
            payload.get("instrument_type") or payload.get("type")
        )

        symbol_info = db.execute(
            text("SELECT sp_get_symbol(:symbol, 168)"),
            {"symbol": symbol},
        ).scalar()
        local_payload = (
            symbol_info.get("payload")
            if symbol_info and symbol_info.get("found")
            else None
        )
        requested_hints = {
            "exchange": requested_exchange,
            "currency": requested_currency,
            "isin": requested_isin,
            "instrument_type": (
                requested_type.value
                if requested_type != InstrumentType.UNKNOWN
                else None
            ),
        }
        local_matches = _matches_listing_hints(local_payload, requested_hints)

        if (
            symbol_info
            and symbol_info.get("source") == "LOCAL"
            and local_matches
            and _has_persisted_identity(local_payload)
            and _has_currency(local_payload)
            and _has_price(local_payload)
        ):
            return {
                "entity_id": str(local_payload["entity_id"]),
                "instrument_id": str(local_payload["instrument_id"]),
                "imported": False,
                "source": "LOCAL",
                "price_available": True,
                "price_refresh_status": "NOT_REQUIRED",
                "price_refresh_job_id": None,
            }

        provider_data_source = _clean_data_source(
            (local_payload or {}).get("data_source") or requested_data_source
        )

        profile_payload = None

        if (
            symbol_info
            and symbol_info.get("source") == "CACHE"
            and isinstance(local_payload, dict)
            and local_payload.get("name")
            and local_payload.get("currency")
            and not requested_isin
            and not requested_currency
            and not requested_exchange
            and requested_type == InstrumentType.UNKNOWN
        ):
            profile_payload = dict(local_payload)

        if profile_payload is None:
            reference = InstrumentReference(
                symbol=symbol,
                isin=requested_isin,
                instrument_type=requested_type,
                currency=requested_currency,
                exchange_code=requested_exchange,
            )
            profile = market_data.get_profile(
                reference,
                required_fields={
                    "name",
                    "currency",
                },
                include_quote=True,
            )
            profile_payload = profile.to_import_payload() if profile else None

            if profile_payload and not _clean_optional(profile_payload.get("currency")):
                raise HTTPException(
                    status_code=422,
                    detail={
                        "code": "INSTRUMENT_CURRENCY_UNRESOLVED",
                        "message": (
                            "Impossibile determinare la valuta dello strumento. "
                            "Lo strumento non è stato importato."
                        ),
                        "symbol": symbol,
                        "exchange": requested_exchange,
                        "data_source": provider_data_source,
                    },
                )

        if not profile_payload:
            instrument_id = (
                local_payload.get("instrument_id")
                if isinstance(local_payload, dict)
                else None
            )
            entity_id = (
                local_payload.get("entity_id")
                if isinstance(local_payload, dict)
                else None
            )

            if local_matches and instrument_id and entity_id:
                job_id = _enqueue_missing_price(
                    db,
                    local_payload,
                    symbol,
                    provider_data_source,
                )
                db.commit()

                return {
                    "entity_id": str(entity_id),
                    "instrument_id": str(instrument_id),
                    "imported": False,
                    "source": symbol_info.get("source"),
                    "price_available": False,
                    "price_refresh_status": "QUEUED",
                    "price_refresh_job_id": job_id,
                }

            raise HTTPException(
                status_code=404,
                detail={
                    "code": "SYMBOL_PROFILE_NOT_FOUND",
                    "message": (
                        "Il simbolo è presente nella cache di ricerca, "
                        "ma non è ancora associato a uno strumento importato."
                    ),
                    "symbol": symbol,
                    "source": (symbol_info.get("source") if symbol_info else None),
                },
            )

        # The user-selected symbol is the listing identity. Shared ISINs may
        # legitimately have several symbols, exchanges and currencies.
        profile_payload = pin_profile_to_listing(
            profile_payload,
            symbol=symbol,
            exchange=requested_exchange,
            currency=requested_currency,
        )
        if requested_isin:
            profile_payload["isin"] = requested_isin
            profile_payload.setdefault("identifiers", {})["ISIN"] = requested_isin
        if requested_type != InstrumentType.UNKNOWN:
            profile_payload["instrument_type"] = requested_type.value

        result = upsert_asset_profile(db, profile_payload)
        if not result:
            raise RuntimeError("sp_upsert_asset_profile returned no row")

        price_available = profile_payload.get("last_price") is not None
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
            "action": result.action,
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
