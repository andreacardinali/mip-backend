MIP shared notification pipeline

Architecture
------------
1. Producers (scheduler jobs or backend routes) call NotificationQueue.enqueue().
2. enqueue resolves the enabled user_sources row and stores source_id in notifications.
3. The producer commits a PENDING DB record and never performs HTTP delivery.
4. jobs.notification_dispatcher claims rows with FOR UPDATE SKIP LOCKED.
5. The dispatcher selects a plugin by channel and updates SENT, RETRY, or FAILED.

New shared files
----------------
notifications/__init__.py
notifications/base.py
notifications/config.py
notifications/models.py
notifications/repository.py
notifications/queue.py
notifications/dispatcher.py
notifications/plugins/__init__.py
notifications/plugins/telegram.py
jobs/notification_dispatcher.py
jobs/notification_dispatcher.job.json
sql/notification_pipeline.sql
env.notifications.example

Files replaced
--------------
jobs/portfolio_intelligence/config.py
jobs/portfolio_intelligence/context.py
jobs/portfolio_intelligence/runtime.py
jobs/portfolio_intelligence/branches/daily_mover.py
jobs/portfolio_intelligence/branches/upcoming_catalyst.py
jobs/portfolio_intelligence/branches/breaking_catalyst.py

Files/directories to delete after extraction
--------------------------------------------
jobs/portfolio_intelligence/notifications/
jobs/portfolio_intelligence/repositories/notifications.py

Installation
------------
1. Extract this archive from /opt/mip/mip-backend.
2. Apply sql/notification_pipeline.sql with ON_ERROR_STOP enabled.
3. Merge env.notifications.example into .env.
4. Delete the obsolete Intelligence-specific notification files listed above.
5. Run compileall.
6. Restart mip-scheduler so notification_dispatcher is registered.
7. Re-export mip_schema.sql after applying the migration.

Commands
--------
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/notification_pipeline.sql
rm -rf jobs/portfolio_intelligence/notifications
rm -f jobs/portfolio_intelligence/repositories/notifications.py
python -m compileall notifications jobs/notification_dispatcher.py jobs/portfolio_intelligence
systemctl restart mip-scheduler

Notes
-----
- source_id is resolved and persisted during enqueue.
- No fallback to administrator Telegram credentials exists.
- Existing legacy rows without source_id are not dispatched automatically.
- Delivery is at-least-once. A process crash after provider delivery but before
  marking SENT can cause a duplicate delivery after stale recovery.
