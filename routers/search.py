import json

from fastapi import APIRouter
from sqlalchemy import text

from db import SessionLocal

from providers.manager import (
    market_data
)

router = APIRouter()


@router.get("")
def search_symbols(q: str):

    if not q:

        return {
            "source": "EMPTY",
            "results": []
        }

    cache_key = (
        q.lower()
         .strip()
    )

    db = SessionLocal()

    try:

        search_result = db.execute(
            text(
                """
                SELECT
                sp_search_assets(
                    :query,
                    20,
                    24
                )
                """
            ),
            {
                "query":
                    cache_key
            },
        ).scalar()

        #
        # LOCAL / CACHE
        #

        if (
            search_result
            and
            search_result["source"]
                != "MISS"
        ):

            return search_result

        #
        # PROVIDER
        #

        results = (
            market_data
            .search_symbols(
                q,
                20
            )
        )

        db.execute(
            text(
                """
                SELECT
                sp_upsert_market_cache(
                    :cache_key,
                    CAST(
                        :payload AS jsonb
                    )
                )
                """
            ),
            {
                "cache_key":
                    cache_key,

                "payload":
                    json.dumps(
                        results
                    ),
            },
        )

        db.commit()

        return {
            "source":
                "PROVIDER",

            "results":
                results,
        }

    finally:

        db.close()
