from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError

from alerts.repository import AlertRepository
from alerts.rule_configuration import validate_rule_payload
from auth.security import CurrentUser, get_current_user
from db import SessionLocal

router = APIRouter()


@router.get("")
def list_rules(current_user: CurrentUser = Depends(get_current_user)):
    database = SessionLocal()
    try:
        return AlertRepository(database).list_user_rules(current_user["user_id"])
    finally:
        database.close()


@router.get("/{rule_id}/events")
def list_events(
    rule_id: UUID,
    limit: int = Query(default=25, ge=1, le=100),
    offset: int = Query(default=0, ge=0, le=100000),
    current_user: CurrentUser = Depends(get_current_user),
):
    database = SessionLocal()
    try:
        return AlertRepository(database).list_user_events(
            current_user["user_id"], str(rule_id), limit=limit, offset=offset
        )
    finally:
        database.close()


def _save_rule(payload: dict, user_id: str, rule_id: UUID | None):
    try:
        validated = validate_rule_payload(payload)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    database = SessionLocal()
    try:
        saved_id = AlertRepository(database).save_user_rule(
            user_id, str(rule_id) if rule_id else None, validated
        )
        if saved_id is None:
            raise HTTPException(status_code=404, detail="Alert rule not found")
        database.commit()
        return {"rule_id": saved_id}
    except IntegrityError as error:
        database.rollback()
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise HTTPException(
                status_code=409, detail="Alert rule name already exists"
            ) from error
        raise
    except Exception:
        database.rollback()
        raise
    finally:
        database.close()


@router.post("")
def create_rule(
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    return _save_rule(payload, current_user["user_id"], None)


@router.put("/{rule_id}")
def update_rule(
    rule_id: UUID,
    payload: dict,
    current_user: CurrentUser = Depends(get_current_user),
):
    return _save_rule(payload, current_user["user_id"], rule_id)
