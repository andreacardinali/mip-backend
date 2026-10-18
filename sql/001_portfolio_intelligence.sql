BEGIN;
ALTER TABLE notifications
    ADD COLUMN IF NOT EXISTS user_id uuid,
    ADD COLUMN IF NOT EXISTS notification_key varchar(255),
    ADD COLUMN IF NOT EXISTS branch varchar(50),
    ADD COLUMN IF NOT EXISTS symbol varchar(50),
    ADD COLUMN IF NOT EXISTS event_key varchar(255),
    ADD COLUMN IF NOT EXISTS severity varchar(20),
    ADD COLUMN IF NOT EXISTS status varchar(20) NOT NULL DEFAULT 'PENDING',
    ADD COLUMN IF NOT EXISTS context jsonb NOT NULL DEFAULT '{}'::jsonb,
    ADD COLUMN IF NOT EXISTS observed_at timestamptz,
    ADD COLUMN IF NOT EXISTS created_at timestamptz NOT NULL DEFAULT now(),
    ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE notifications
    DROP CONSTRAINT IF EXISTS notifications_user_id_fkey;
ALTER TABLE notifications
    ADD CONSTRAINT notifications_user_id_fkey
    FOREIGN KEY (user_id) REFERENCES users(user_id) ON DELETE CASCADE;

CREATE UNIQUE INDEX IF NOT EXISTS uq_notifications_user_key
    ON notifications(user_id, notification_key)
    WHERE notification_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_notifications_user_sent
    ON notifications(user_id, sent_at DESC);
CREATE INDEX IF NOT EXISTS ix_notifications_user_branch
    ON notifications(user_id, branch);
CREATE INDEX IF NOT EXISTS ix_notifications_user_symbol
    ON notifications(user_id, symbol);

DROP TRIGGER IF EXISTS trg_notifications_timestamps ON notifications;
CREATE TRIGGER trg_notifications_timestamps
BEFORE INSERT OR UPDATE ON notifications
FOR EACH ROW EXECUTE FUNCTION trg_set_timestamps();
COMMIT;
