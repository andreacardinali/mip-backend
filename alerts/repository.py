from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from alerts.models import AlertRule


class AlertRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def list_enabled_rules(self) -> list[AlertRule]:
        rows = self.db.execute(
            text("SELECT * FROM public.sp_alert_list_enabled_rules()")
        ).mappings()
        return [
            AlertRule(
                str(r["rule_id"]),
                str(r["user_id"]),
                str(r["name"]),
                str(r["alert_type"]),
                str(r["severity"]),
                str(r["channel"]) if r["channel"] else None,
                dict(r["conditions"] or {}),
                int(r["cooldown_minutes"]),
            )
            for r in rows
        ]

    def register_match(
        self,
        *,
        rule_id: str,
        instrument_id: str,
        signal_id: str,
        fingerprint: str,
        payload: dict[str, Any],
    ) -> str | None:
        v = self.db.execute(
            text(
                "SELECT public.sp_alert_register_match(CAST(:rule_id AS uuid),CAST(:instrument_id AS uuid),CAST(:signal_id AS uuid),:fingerprint,CAST(:payload AS jsonb))"
            ),
            {
                "rule_id": rule_id,
                "instrument_id": instrument_id,
                "signal_id": signal_id,
                "fingerprint": fingerprint,
                "payload": json.dumps(payload, default=str),
            },
        ).scalar()
        return str(v) if v else None

    def mark_enqueued(self, *, alert_event_id: str, notification_id: str) -> None:
        self.db.execute(
            text(
                "SELECT public.sp_alert_mark_enqueued(CAST(:event_id AS uuid),CAST(:notification_id AS uuid))"
            ),
            {"event_id": alert_event_id, "notification_id": notification_id},
        )
