# MIP Market Analysis strategies

Pacchetto per replacement in-place dalla root di `mip-backend`.

## Architettura

- Le strategie sono funzioni di dominio pure e non eseguono HTTP, SQL o chiamate LLM.
- I dati arrivano esclusivamente attraverso `MarketDataService`, già usato dallo scan.
- Retry HTTP, provider LLM, cache, notifiche e database restano nei moduli condivisi root esistenti.
- Non sono introdotti client HTTP, provider, cache o logiche di retry duplicate.

## File sostituiti

- `market_analysis/models.py`
- `market_analysis/technical.py`
- `market_analysis/strategies/__init__.py`
- `market_analysis/strategies/base.py`
- `market_analysis/strategies/registry.py`

## File aggiunti

- `market_analysis/strategies/common.py`
- `market_analysis/strategies/trend_pullback.py`
- `market_analysis/strategies/supertrend.py`
- `market_analysis/strategies/smart_money_structure.py`
- `market_analysis/strategies/deep_oversold.py`
- `tests/test_market_analysis_strategy_registry.py`

Il precedente `market_analysis/strategies/implementations.py` non viene piu importato. Puo essere rimosso dopo la validazione.

## Installazione

```bash
cd /opt/mip/mip-backend
unzip -o mip_market_analysis_strategies.zip
python -m py_compile market_analysis/models.py market_analysis/technical.py market_analysis/strategies/*.py
pytest -q tests/test_market_analysis_strategy_registry.py tests/test_market_analysis_scan.py
pytest -q
```
