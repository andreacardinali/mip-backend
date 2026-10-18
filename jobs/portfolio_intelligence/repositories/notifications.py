import json
from datetime import datetime, timedelta, timezone
from sqlalchemy import text
from sqlalchemy.orm import Session

class NotificationRepository:
    def __init__(self, db: Session): self.db=db
    def exists(self, user_id: str, notification_key: str) -> bool:
        return bool(self.db.execute(text("""
            SELECT 1 FROM notifications
            WHERE user_id=CAST(:user_id AS uuid)
              AND notification_key=:notification_key AND status='SENT'
        """), {"user_id":user_id,"notification_key":notification_key}).scalar())
    def similar(self, user_id: str, branch: str, symbol: str|None, days: int=30, limit: int=10) -> list[dict]:
        return [dict(r) for r in self.db.execute(text("""
            SELECT notification_id, notification_key, title, message, context, sent_at
            FROM notifications
            WHERE user_id=CAST(:user_id AS uuid) AND branch=:branch AND status='SENT'
              AND (:symbol IS NULL OR symbol=:symbol)
              AND sent_at >= :cutoff ORDER BY sent_at DESC LIMIT :limit
        """), {"user_id":user_id,"branch":branch,"symbol":symbol,
                 "cutoff":datetime.now(timezone.utc)-timedelta(days=days),"limit":limit}).mappings()]
    def telegram_config(self, user_id: str) -> dict:
        row=self.db.execute(text("""
            SELECT config FROM user_sources
            WHERE user_id=CAST(:user_id AS uuid)
              AND UPPER(source_type)='TELEGRAM'
            ORDER BY created_at DESC LIMIT 1
        """), {"user_id":user_id}).scalar()
        return dict(row or {})
    def insert(self, *, user_id: str, notification_key: str, branch: str,
               entity_id: str|None, symbol: str|None, event_key: str|None,
               severity: str, title: str, message: str, context: dict,
               status: str, sent_at: datetime|None):
        self.db.execute(text("""
            INSERT INTO notifications
            (user_id, entity_id, notification_type, notification_key, branch,
             symbol, event_key, severity, status, title, message, context,
             observed_at, sent_at, created_at, updated_at)
            VALUES (CAST(:user_id AS uuid),CAST(:entity_id AS uuid),:branch,
                    :notification_key,:branch,:symbol,:event_key,:severity,:status,
                    :title,:message,CAST(:context AS jsonb),now(),:sent_at,now(),now())
            ON CONFLICT (user_id, notification_key) DO UPDATE SET
              status=EXCLUDED.status, message=EXCLUDED.message,
              context=EXCLUDED.context, sent_at=EXCLUDED.sent_at, updated_at=now()
        """), {"user_id":user_id,"notification_key":notification_key,
        "branch":branch,"entity_id":entity_id,"symbol":symbol,"event_key":event_key,
        "severity":severity,"status":status,"title":title,"message":message,
        "context":json.dumps(context,default=str),"sent_at":sent_at})
    def purge(self, days: int) -> int:
        result=self.db.execute(text("""
            DELETE FROM notifications
            WHERE status='SENT' AND sent_at < :cutoff
              AND branch IN ('DAILY_MOVER','UPCOMING_CATALYST','BREAKING_CATALYST')
        """), {"cutoff":datetime.now(timezone.utc)-timedelta(days=days)})
        return result.rowcount or 0
