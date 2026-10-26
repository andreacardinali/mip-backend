# MIP single ranking formula

Pacchetto in-place costruito sul commit `8012a0aea4816d513ddc2085ce4ccfa509bcad40`.

## Contenuto

- `market_screener/constants.py`: unica fonte dei parametri stabili.
- `market_screener/config.py`: configurazione deterministica senza letture da `.env`.
- `market_screener/ranking.py`: formula unica; technical e quant obbligatori; attention e LLM neutrali a 50 quando assenti.
- worker Market Analysis e Discovery: importano periodo, timeframe, soglie e limiti dal modulo constants.
- worker ranking: non legge più pesi, limiti o label di algoritmo da `.env`.

## Formula

```text
base = attention * 0.20
     + technical * 0.25
     + quant * 0.35
     + llm * 0.20

final = clamp(
    base
    + 8 * consensus_score / 100
    - 12 * conflict_score / 100,
    0,
    100,
)
```

Il correttivo di consenso si applica solo con almeno due strategie. Se manca `technical_score` o `quant_score`, l'opportunità non entra nel ranking.

## Installazione

Estrarre dalla root di `mip-backend`:

```bash
unzip -o mip-ranking-single-formula-inplace.zip
```

Rimuovere dal `.env` le variabili `MARKET_SCREENER_*WEIGHT`, `MARKET_SCREENER_MINIMUM_SCORE`, `MARKET_SCREENER_LIMIT`, `MARKET_SCREENER_ALGORITHM_VERSION` e `MARKET_SCREENER_SIGNAL_ALGORITHM_VERSION`.
