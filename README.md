# Commit per simbolo per Market Analysis LLM Enrichment

Questa patch rimuove la transazione unica intorno all'intero job.

Nuovo flusso:

1. carica i segnali pendenti in una transazione breve;
2. chiude la transazione;
3. esegue la chiamata LLM senza transazioni DB aperte;
4. persiste ogni singolo segnale con un nuovo `run_transactional()`;
5. committa immediatamente il risultato del simbolo;
6. continua con i simboli successivi anche se uno fallisce.

Non richiede migrazioni SQL.

```bash
cd /opt/mip/mip-backend
unzip -o mip_llm_enrichment_commit_per_symbol.zip
python -m pytest -q \
  tests/test_market_analysis_llm_enrichment.py \
  tests/test_market_analysis_llm_enrichment_transactions.py
python -m jobs.market_analysis_llm_enrichment.worker
```
