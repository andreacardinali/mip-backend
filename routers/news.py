from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text

from auth.security import CurrentUser, get_current_user
from db import SessionLocal

router = APIRouter()


def _serialize(row) -> dict:
    result = dict(row._mapping)
    for key in ("news_id", "cluster_id"):
        if result.get(key) is not None:
            result[key] = str(result[key])
    return result


@router.get("")
def get_news(
    scope: Annotated[str, Query()] = "MY_ASSETS",
    date_from: Annotated[datetime | None, Query()] = None,
    date_to: Annotated[datetime | None, Query()] = None,
    source: Annotated[str | None, Query(max_length=100)] = None,
    sentiment_min: Annotated[float | None, Query(ge=-1, le=1)] = None,
    sentiment_max: Annotated[float | None, Query(ge=-1, le=1)] = None,
    impact_min: Annotated[float | None, Query()] = None,
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
                FROM public.sp_news_get_user_news(
                    CAST(:user_id AS uuid),
                    :scope,
                    :date_from,
                    :date_to,
                    :source,
                    :sentiment_min,
                    :sentiment_max,
                    :impact_min,
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
                "source": source,
                "sentiment_min": sentiment_min,
                "sentiment_max": sentiment_max,
                "impact_min": impact_min,
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
def get_news_facets(
    scope: Annotated[str, Query()] = "MY_ASSETS",
    current_user: CurrentUser = Depends(get_current_user),
):
    db = SessionLocal()
    try:
        return db.execute(
            text(
                """
                SELECT public.sp_news_get_user_news_facets(
                    CAST(:user_id AS uuid),
                    :scope
                )
                """
            ),
            {"user_id": current_user["user_id"], "scope": scope},
        ).scalar_one()
    except Exception as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    finally:
        db.close()
