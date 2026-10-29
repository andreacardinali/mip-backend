from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from alerts.models import AlertRule


class AlertRepository:
    def instrument_exchange(self, instrument_id: str) -> str | None:
        return self.db.execute(
            text("SELECT public.sp_alert_instrument_exchange(CAST(:instrument_id AS uuid))"),
            {"instrument_id": instrument_id},
        ).scalar()

    def session_reference(self, instrument_id: str, session_date) -> dict[str, Any] | None:
        value = self.db.execute(
            text("SELECT public.sp_alert_session_reference(CAST(:instrument_id AS uuid), CAST(:session_date AS date))"),
            {"instrument_id": instrument_id, "session_date": session_date},
        ).scalar()
        return dict(value) if value else None

    def observe_threshold(self, *, rule_id: str, instrument_id: str,
                          observed_at: str, matched: bool, state_key: str) -> int | None:
        return self.db.execute(
            text("SELECT public.sp_alert_observe_threshold(CAST(:rule_id AS uuid), CAST(:instrument_id AS uuid), CAST(:observed_at AS timestamptz), :matched, :state_key)"),
            {"rule_id": rule_id, "instrument_id": instrument_id,
             "observed_at": observed_at, "matched": matched, "state_key": state_key},
        ).scalar()

    def list_user_events(
        self, user_id: str, rule_id: str, *, limit: int = 25, offset: int = 0
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                "SELECT public.sp_alert_list_user_events("
                "CAST(:user_id AS uuid), CAST(:rule_id AS uuid), :limit, :offset)"
            ),
            {"user_id": user_id, "rule_id": rule_id, "limit": limit, "offset": offset},
        ).scalars()
        return [dict(row) for row in rows]

    def __init__(self, db: Session) -> None:
        self.db = db

    def list_user_rules(self, user_id: str) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                "SELECT * FROM public.sp_alert_list_user_rules(CAST(:user_id AS uuid))"
            ),
            {"user_id": user_id},
        ).mappings()
        return [
            {
                **dict(row),
                "rule_id": str(row["rule_id"]),
            }
            for row in rows
        ]

    def save_user_rule(
        self, user_id: str, rule_id: str | None, payload: dict[str, Any]
    ) -> str | None:
        saved_id = self.db.execute(
            text(
                "SELECT public.sp_alert_upsert_rule("
                "CAST(:rule_id AS uuid), CAST(:user_id AS uuid), :name, "
                ":alert_type, :severity, :channel, CAST(:conditions AS jsonb), "
                ":cooldown_minutes, :enabled)"
            ),
            {
                "rule_id": rule_id,
                "user_id": user_id,
                "name": payload["name"],
                "alert_type": payload.get("alert_type", "MARKET_SIGNAL"),
                "severity": payload["severity"],
                "channel": payload["channel"],
                "conditions": json.dumps(payload["conditions"]),
                "cooldown_minutes": payload["cooldown_minutes"],
                "enabled": payload["enabled"],
            },
        ).scalar()
        return str(saved_id) if saved_id else None

    def set_user_rule_enabled(self, user_id: str, rule_id: str, enabled: bool) -> bool:
        return bool(
            self.db.execute(
                text(
                    "SELECT public.sp_alert_set_rule_enabled("
                    "CAST(:rule_id AS uuid), CAST(:user_id AS uuid), :enabled)"
                ),
                {"rule_id": rule_id, "user_id": user_id, "enabled": enabled},
            ).scalar()
        )

    def delete_user_rule(self, user_id: str, rule_id: str) -> bool:
        return bool(
            self.db.execute(
                text(
                    "SELECT public.sp_alert_delete_rule("
                    "CAST(:rule_id AS uuid), CAST(:user_id AS uuid))"
                ),
                {"rule_id": rule_id, "user_id": user_id},
            ).scalar()
        )

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

    def list_rule_candidates(
        self, rule_id: str, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        rows = self.db.execute(
            text(
                "SELECT public.sp_alert_list_rule_candidates("
                "CAST(:rule_id AS uuid), :limit)"
            ),
            {"rule_id": rule_id, "limit": limit},
        ).scalars()
        return [dict(row) for row in rows]

    def register_match(
        self,
        *,
        rule_id: str,
        instrument_id: str,
        signal_id: str | None,
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

    def mark_notification_skipped(self, *, alert_event_id: str, status: str) -> None:
        self.db.execute(
            text(
                "SELECT public.sp_alert_mark_notification_skipped("
                "CAST(:event_id AS uuid), :status)"
            ),
            {"event_id": alert_event_id, "status": status},
        )

    def mark_enqueued(self, *, alert_event_id: str, notification_id: str) -> None:
        self.db.execute(
            text(
                "SELECT public.sp_alert_mark_enqueued(CAST(:event_id AS uuid),CAST(:notification_id AS uuid))"
            ),
            {"event_id": alert_event_id, "notification_id": notification_id},
        )
