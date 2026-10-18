File nuovi:
- providers/exchange_rates/__init__.py
- providers/exchange_rates/base.py
- providers/exchange_rates/ecb.py
- services/exchange_rates.py
- jobs/exchange_rates/__init__.py
- jobs/exchange_rates/refresh_exchange_rates.py
- jobs/refresh_exchange_rates.job.json
- scripts/refresh_exchange_rates.py
- sql/exchange_rates.sql
- env.exchange_rates.example

File da cancellare:
- nessuno

Installazione:
1. Estrarre dalla root del backend.
2. Applicare sql/exchange_rates.sql al database.
3. Aggiungere EXCHANGE_RATES_HTTP_TIMEOUT=30 al file .env.
4. Test CLI: python -m scripts.refresh_exchange_rates
5. Riavviare mip-scheduler per l'auto-registrazione del job.
6. Riesportare mip_schema.sql dopo l'applicazione dello SQL.
