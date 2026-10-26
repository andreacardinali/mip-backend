# LLM thinking configurabile per prefisso

File completi per replacement in-place dalla root di `mip-backend`.

## Installazione

```bash
cd /opt/mip/mip-backend
unzip -o mip_llm_think_full_files.zip
python -m pytest -q tests/test_llm_think_config.py tests/test_ollama_structured_output.py
```

## Configurazione

Il default e `false`, quindi non e necessario aggiungere variabili.

```dotenv
MARKET_ANALYSIS_LLM_THINK=false
```

Per abilitarlo solo in un consumer specifico:

```dotenv
PORTFOLIO_INTELLIGENCE_LLM_THINK=true
```
