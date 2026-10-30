from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal

router = APIRouter()


@router.get("/{entity_id}/overview")
def get_entity_overview(
    entity_id: UUID,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        payload = db.execute(
            text(
                "SELECT public.sp_get_user_entity_overview("
                "CAST(:user_id AS uuid), CAST(:entity_id AS uuid))"
            ),
            {"user_id": current_user["user_id"], "entity_id": str(entity_id)},
        ).scalar_one_or_none()
        if payload is None:
            raise HTTPException(404, "Entity not found")
        return payload
    finally:
        db.close()


@router.get("")
async def get_entities(
    current_user: CurrentUser = Depends(get_current_user),
    scope: Literal["ALL", "PORTFOLIO", "WATCHLIST", "SIGNALS"] = "ALL",
    query: str = "",
):
    db = SessionLocal()

    try:
        rows = db.execute(
            text(
                """
                SELECT *
                FROM public.sp_get_user_entities(
                    CAST(:user_id AS uuid), CAST(:scope AS varchar),
                    CAST(:query AS text), 500
                )
                """
            ),
            {"user_id": current_user["user_id"], "scope": scope, "query": query.strip()},
        )

        return [dict(row._mapping) for row in rows]

    finally:
        db.close()
