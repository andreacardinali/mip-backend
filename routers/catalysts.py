from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal

router = APIRouter()


def _serialize(row) -> dict:
    result = dict(row._mapping)
    for key in ("catalyst_id", "entity_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    return result


@router.get("")
def get_catalysts(
    scope: Annotated[str, Query()] = "MY_ASSETS",
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    catalyst_type: Annotated[str | None, Query()] = None,
    status: Annotated[str | None, Query()] = None,
    importance_min: Annotated[float | None, Query(ge=0)] = None,
    query: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=2000)] = 500,
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                """
                SELECT *
                FROM public.sp_get_user_catalysts(
                    CAST(:user_id AS uuid),
                    :scope,
                    :date_from,
                    :date_to,
                    :catalyst_type,
                    :status,
                    :importance_min,
                    :query,
                    :limit
                )
                """
            ),
            {
                "user_id": current_user["user_id"],
                "scope": scope,
                "date_from": date_from,
                "date_to": date_to,
                "catalyst_type": catalyst_type,
                "status": status,
                "importance_min": importance_min,
                "query": query,
                "limit": limit,
            },
        ).all()
        return [_serialize(row) for row in rows]
    except Exception as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        db.close()


@router.get("/facets")
def get_catalyst_facets(
    scope: Annotated[str, Query()] = "MY_ASSETS",
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        value = db.execute(
            text(
                """
                SELECT public.sp_get_user_catalyst_facets(
                    CAST(:user_id AS uuid),
                    :scope
                )
                """
            ),
            {"user_id": current_user["user_id"], "scope": scope},
        ).scalar_one()
        return value
    except Exception as error:
        import traceback

        traceback.print_exc()

        raise HTTPException(
            status_code=422,
            detail=str(error)
        ) from error
    finally:
        db.close()
