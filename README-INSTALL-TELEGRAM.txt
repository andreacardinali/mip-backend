Architettura credenziali Telegram

1. .env
   Contiene esclusivamente TELEGRAM_BOT_TOKEN e TELEGRAM_CHAT_ID
   dell'amministratore. Sono usati da TelegramSender.for_platform_admin()
   e dallo script scripts/test_telegram.py.

2. user_sources
   Le notifiche applicative degli utenti leggono esclusivamente:
   - config.bot_token oppure config.token
   - config.chat_id oppure external_id

   Non esiste fallback dalle credenziali utente alle credenziali admin.
   Se user_sources non e configurato, l'invio applicativo fallisce in modo esplicito.

File nuovi:
- notifications/__init__.py
- notifications/base.py
- notifications/telegram.py
- scripts/test_telegram.py
- env.telegram.example

File da sostituire:
- jobs/portfolio_intelligence/notifications/telegram.py
- jobs/portfolio_intelligence/notifications/service.py
- jobs/portfolio_intelligence/runtime.py

Installazione:
1. Estrarre dalla root /opt/mip/mip-backend.
2. Test admin: python -m scripts.test_telegram
3. Compilazione: python -m compileall notifications jobs/portfolio_intelligence/notifications jobs/portfolio_intelligence/runtime.py scripts/test_telegram.py

Configurazione user_sources di esempio:

UPDATE user_sources
SET external_id = '<CHAT_ID>',
    config = jsonb_build_object(
        'enabled', true,
        'bot_token', '<TOKEN>'
    )
WHERE user_id = '<USER_UUID>'::uuid
  AND source_type = 'TELEGRAM';
