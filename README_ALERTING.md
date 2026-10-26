# MIP Market Alerting

Pacchetto in-place per la root di `mip-backend`.

## Installazione
1. Estrarre lo ZIP nella root di `mip-backend`, mantenendo i percorsi.
2. Applicare `sql/20261026_market_alerting.sql` al database MIP.
3. Copiare le variabili di `.env.alerting.example` nel `.env` effettivo.
4. Riavviare backend e scheduler.
5. Abilitare il job `market_alert_evaluation` dalla GUI amministrativa.

## Regola iniziale di esempio
```sql
SELECT public.sp_alert_upsert_rule(
  NULL,
  '<USER_ID>'::uuid,
  'High conviction market signals',
  'MARKET_SIGNAL',
  'HIGH',
  NULL,
  '{"directions":["BUY","STRONG_BUY"],"min_composite_score":0.80,"min_consensus_score":0.60,"max_conflict_score":0.35,"min_strategy_count":2}'::jsonb,
  240,
  true
);
```
Con `channel = NULL`, la coda condivisa risolve canale e sorgente dalle preferenze di notifica dell'utente.

## Comportamento
- Valutazione dei segnali correnti del market screener.
- Filtri per simbolo, direzione, verdict, origine, score, consenso, conflitto e numero strategie.
- Deduplicazione per stato del segnale.
- Cooldown per regola e strumento.
- Persistenza degli eventi e collegamento alla notification queue esistente.
- Invio demandato al dispatcher già presente, quindi nessun invio diretto dal job.
