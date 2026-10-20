# OpenFIGI primary exchange code

- OpenFIGI `exchCode` populates both `exchange` and `openfigi_exch_code`.
- MIC is optional and is not part of `MISSING`.
- `(symbol, exchange)` is a normal index, not a uniqueness constraint.
- FIGI is the preferred unique external identifier.
- FMP remains an explicit `full_refresh` fallback.

```bash
cd /opt/mip/mip-backend
unzip -o mip-openfigi-primary-exchange.zip -d /opt/mip/mip-backend
psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f sql/011_openfigi_primary_exchange.sql
python -m py_compile providers/openfigi.py providers/manager.py
pytest -q tests/test_openfigi_primary_exchange.py
./scripts/check.sh
python -m scripts.refresh_entities --mode MISSING --debug
```
