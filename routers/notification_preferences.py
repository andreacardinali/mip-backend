from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal
from notifications.credentials import encrypt_credential
from notifications.models import NotificationDelivery
from notifications.providers.telegram import TelegramProvider

router = APIRouter()
SUPPORTED_CHANNELS = [
    {
        "channel": "TELEGRAM",
        "label": "Telegram",
        "destination_label": "Chat ID",
        "credential_label": "Bot token",
    }
]


def _serialize_row(row) -> dict:
    return {
        key: str(value) if key.endswith("_id") and value is not None else value
        for key, value in row._mapping.items()
    }


def _get_source_config(db, user_id: str, source_id: str):
    return (
        db.execute(
            text(
                """
                SELECT *
                FROM public.sp_get_user_notification_source_config(
                    CAST(:user_id AS uuid), CAST(:source_id AS uuid)
                )
                """
            ),
            {"user_id": user_id, "source_id": source_id},
        )
        .mappings()
        .first()
    )


@router.get("")
def get_notification_preferences(
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        preferences = db.execute(
            text(
                "SELECT * FROM public.sp_get_user_notification_preferences("
                "CAST(:user_id AS uuid))"
            ),
            {"user_id": current_user["user_id"]},
        ).all()
        sources = db.execute(
            text(
                "SELECT * FROM public.sp_get_user_notification_sources("
                "CAST(:user_id AS uuid))"
            ),
            {"user_id": current_user["user_id"]},
        ).all()
        return {
            "preferences": [_serialize_row(row) for row in preferences],
            "sources": [_serialize_row(row) for row in sources],
            "supported_channels": SUPPORTED_CHANNELS,
        }
    finally:
        db.close()


@router.put("")
def update_notification_preferences(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    preferences = payload.get("preferences")
    if not isinstance(preferences, list):
        raise HTTPException(status_code=422, detail="preferences must be an array")

    db = SessionLocal()
    try:
        for preference in preferences:
            if not isinstance(preference, dict):
                raise HTTPException(status_code=422, detail="Invalid preference")
            db.execute(
                text(
                    """
                    SELECT public.sp_upsert_user_notification_preference(
                        CAST(:user_id AS uuid), :notification_type, :enabled,
                        CAST(:source_id AS uuid), :severity_min
                    )
                    """
                ),
                {
                    "user_id": current_user["user_id"],
                    "notification_type": preference.get("notification_type"),
                    "enabled": bool(preference.get("enabled")),
                    "source_id": preference.get("source_id"),
                    "severity_min": preference.get("severity_min"),
                },
            ).scalar_one()
        db.commit()
    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        db.close()
    return get_notification_preferences(current_user)


@router.post("/sources")
def create_notification_source(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    return _save_source(payload, current_user, source_id=None)


@router.put("/sources/{source_id}")
def update_notification_source(
    source_id: str,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    return _save_source(payload, current_user, source_id=source_id)


def _save_source(payload: dict, current_user: CurrentUser, source_id: str | None):
    db = SessionLocal()
    try:
        existing = (
            _get_source_config(db, current_user["user_id"], source_id)
            if source_id
            else None
        )
        if source_id and existing is None:
            raise HTTPException(status_code=404, detail="Notification source not found")

        source_type = str(payload.get("source_type") or "").upper().strip()
        if source_type != "TELEGRAM":
            raise HTTPException(status_code=422, detail="Unsupported source type")

        config: dict[str, Any] = dict(existing["config"] or {}) if existing else {}
        config.update(
            {
                "enabled": bool(payload.get("enabled", True)),
                "parse_mode": payload.get("parse_mode") or None,
                "disable_web_page_preview": bool(
                    payload.get("disable_web_page_preview", True)
                ),
            }
        )
        credential = str(payload.get("credential") or "").strip()
        if credential:
            config["credential_encrypted"] = encrypt_credential(credential)
            config.pop("bot_token", None)
            config.pop("token", None)
        elif not existing or not any(
            config.get(key) for key in ("credential_encrypted", "bot_token", "token")
        ):
            raise HTTPException(status_code=422, detail="Bot token is required")

        external_id = str(payload.get("external_id") or "").strip()
        if not external_id and existing:
            external_id = str(existing["external_id"] or "").strip()

        saved_id = db.execute(
            text(
                """
                SELECT public.sp_upsert_user_notification_source(
                    CAST(:user_id AS uuid), CAST(:source_id AS uuid),
                    :source_type, :source_name, :external_id,
                    CAST(:config AS jsonb)
                )
                """
            ),
            {
                "user_id": current_user["user_id"],
                "source_id": source_id,
                "source_type": source_type,
                "source_name": payload.get("source_name"),
                "external_id": external_id,
                "config": __import__("json").dumps(config),
            },
        ).scalar_one()
        db.commit()
        return {"source_id": str(saved_id)}
    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        db.close()


@router.delete("/sources/{source_id}")
def delete_notification_source(
    source_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        deleted = db.execute(
            text(
                "SELECT public.sp_delete_user_notification_source("
                "CAST(:user_id AS uuid), CAST(:source_id AS uuid))"
            ),
            {"user_id": current_user["user_id"], "source_id": source_id},
        ).scalar_one()
        if not deleted:
            raise HTTPException(status_code=404, detail="Notification source not found")
        db.commit()
        return {"deleted": True}
    except HTTPException:
        db.rollback()
        raise
    except Exception as error:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(error)) from error
    finally:
        db.close()


@router.post("/sources/{source_id}/test")
def test_notification_source(
    source_id: str,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        source = _get_source_config(db, current_user["user_id"], source_id)
        if source is None:
            raise HTTPException(status_code=404, detail="Notification source not found")
        if source["source_type"] != "TELEGRAM":
            raise HTTPException(status_code=422, detail="Unsupported source type")
        provider = TelegramProvider(timeout_seconds=15)
        result = provider.send(
            NotificationDelivery(
                notification_id="source-test",
                user_id=str(current_user["user_id"]),
                source_id=str(source["source_id"]),
                channel="TELEGRAM",
                notification_key="source-test",
                notification_type="SOURCE_TEST",
                severity="LOW",
                title="Test MIP",
                message="MIP: destinazione configurata correttamente.",
                context={},
                source_external_id=source["external_id"],
                source_config=dict(source["config"] or {}),
                attempt_count=0,
            )
        )
        return {"status": "SUCCESS", "provider_message_id": result.provider_message_id}
    except HTTPException:
        raise
    except Exception as error:
        raise HTTPException(status_code=502, detail=str(error)) from error
    finally:
        db.close()
