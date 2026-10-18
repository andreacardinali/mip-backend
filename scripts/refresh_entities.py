import json
from providers.manager import (
    market_data
)

from sqlalchemy import text

from db import SessionLocal


def refresh_entities(
    mode: str = "MISSING",
    symbol: str | None = None,
    create_if_missing: bool = False,
    full_refresh: bool = False,
):

    db = SessionLocal()

    try:

        rows = db.execute(
            text(
                """
                SELECT *
                FROM
                sp_get_entities_for_refresh(
                    :mode,
                    :symbol
                )
                """
            ),
            {
                "mode": mode,
                "symbol": symbol,
            },
        ).fetchall()

        updated = 0
        created = 0

        #
        # create if missing
        #

        if (
            mode == "SYMBOL"
            and
            symbol
            and
            len(rows) == 0
            and
            create_if_missing
        ):

            profile = (
                market_data
                .get_profile(
                    symbol
                )
            )

            print(
                symbol,
                profile.get("last_price") if profile else None,
                profile.get("data_source") if profile else None,
                flush=True
            )
            
            if not profile:

                return {
                    "updated": 0,
                    "created": 0,
                    "error": "symbol not found",
                }

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
                {
                    "payload":
                        json.dumps(
                            profile
                        )
                },
            ).first()

            db.commit()

            return {
                "updated": 0,
                "created": 1,
                "entity_id":
                    str(
                        result.entity_id
                    ),
                "instrument_id":
                    str(
                        result.instrument_id
                    ),
            }

        #
        # refresh
        #

        for row in rows:

            try:

                print(
                    f"Refreshing {row.symbol}",
                    flush=True
                )

                profile = (
                    market_data
                    .get_profile(
                        row.symbol,
                        full_refresh=full_refresh
                    )
                )

                print(
                    row.symbol,
                    profile.get("last_price") if profile else None,
                    profile.get("data_source") if profile else None,
                    profile.get("country_code") if profile else None,
                    flush=True
                )

                if not profile:
                    continue

                print(
                    "REFRESHING ENTITY",
                    row.entity_id,
                    row.symbol,
                    profile.get("data_source"),
                    profile.get("currency"),
                    profile.get("country_code"),
                    flush=True
                )

                db.execute(
                    text(
                        """
                        SELECT
                        sp_refresh_entity_from_profile(
                            :entity_id,
                            :instrument_id,
                            CAST(
                                :payload
                                AS jsonb
                            )
                        )
                        """
                    ),
                    {
                        "entity_id":
                            row.entity_id,
                        "instrument_id":
                            row.instrument_id,
                        "payload":
                            json.dumps(
                                profile
                            ),
                    },
                )

                updated += 1
                
                print(
                    "UPDATED",
                    row.symbol,
                    flush=True
                )

            except Exception as ex:

                print(
                    f"ERROR {row.symbol}: {ex}"
                )

        db.commit()

        return {
            "mode": mode,
            "updated": updated,
            "created": created,
        }

    finally:

        db.close()


if __name__ == "__main__":

    result = refresh_entities()

    print(result)