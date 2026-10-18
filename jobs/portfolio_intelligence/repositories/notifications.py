import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from sqlalchemy.orm import Session

class NotificationRepository:

    def notification_exists(
        self,
        user_id: str,
        notification_key: str,
    ) -> bool:
        return bool(
            self.db.execute(
                text(
                    "SELECT "
                    "sp_notification_exists("
                    "CAST(:user_id AS uuid), "
                    ":notification_key"
                    ")"
                ),
                {
                    "user_id": user_id,
                    "notification_key":
                        notification_key,
                },
            ).scalar()
        )

    def get_delivery_source(
        self,
        user_id: str,
        source_type: str,
    ) -> dict:
        row = self.db.execute(
            text(
                "SELECT * FROM "
                "sp_get_user_notification_source("
                "CAST(:user_id AS uuid), "
                ":source_type"
                ")"
            ),
            {
                "user_id": user_id,
                "source_type": source_type,
            },
        ).mappings().first()

        return dict(row or {})