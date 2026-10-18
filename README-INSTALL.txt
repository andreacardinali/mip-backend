File nuovi:
- services/__init__.py
- services/entities.py
- jobs/entities/__init__.py
- jobs/entities/refresh_entities.py
- jobs/refresh_entities.job.json

File da sostituire:
- routers/admin.py
- scripts/refresh_entities.py

File da cancellare:
- nessuno

Nota: scripts/refresh_entities.py resta come adattatore CLI e non contiene piu la logica applicativa.
