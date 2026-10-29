BEGIN;

CREATE OR REPLACE FUNCTION public.sp_market_analysis_list_universe_members(p_universe_id uuid)
RETURNS TABLE(universe_id uuid, instrument_id uuid, entity_id uuid, symbol character varying, exchange character varying, currency_code character, isin character varying)
LANGUAGE sql STABLE AS $$
SELECT p_universe_id AS universe_id, i.instrument_id, i.entity_id, i.symbol,
    i.exchange, i.currency_code,
    (SELECT id.identity_value
     FROM public.instrument_identities ii
     JOIN public.identities id ON id.identity_id = ii.identity_id
     JOIN public.identity_types it ON it.identity_type_id = id.identity_type_id
     WHERE ii.instrument_id = i.instrument_id AND it.target_kind = 'INSTRUMENT'
       AND UPPER(it.code) = 'ISIN'
     ORDER BY ii.is_primary DESC, ii.verified_at DESC NULLS LAST,
         ii.created_at DESC, id.identity_id LIMIT 1) AS isin
FROM public.instruments i
WHERE EXISTS (SELECT 1 FROM public.market_universe_members mum
    WHERE mum.instrument_id = i.instrument_id AND mum.universe_id = p_universe_id
      AND mum.valid_to IS NULL)
 OR EXISTS (SELECT 1 FROM public.watchlist_items wi WHERE wi.entity_id = i.entity_id)
 OR EXISTS (SELECT 1 FROM public.positions pos
    WHERE pos.instrument_id = i.instrument_id AND pos.quantity <> 0)
ORDER BY i.symbol, i.exchange NULLS LAST, i.instrument_id;
$$;

CREATE TABLE IF NOT EXISTS public.alert_threshold_state (
  rule_id uuid NOT NULL REFERENCES public.alert_rules(rule_id) ON DELETE CASCADE,
  instrument_id uuid NOT NULL REFERENCES public.instruments(instrument_id) ON DELETE CASCADE,
  observed_at timestamptz NOT NULL,
  matched boolean NOT NULL,
  state_key text NOT NULL,
  generation bigint NOT NULL DEFAULT 1,
  PRIMARY KEY (rule_id, instrument_id)
);

CREATE OR REPLACE FUNCTION public.sp_alert_instrument_exchange(p_instrument_id uuid) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT exchange::text FROM public.instruments WHERE instrument_id=p_instrument_id
$$;

CREATE OR REPLACE FUNCTION public.sp_alert_session_reference(p_instrument_id uuid, p_session_date date) RETURNS jsonb
LANGUAGE sql STABLE AS $$
  SELECT jsonb_build_object('close', close, 'ts', ts)
  FROM public.price_history
  WHERE instrument_id=p_instrument_id AND close>0
    AND (ts AT TIME ZONE 'UTC')::date=p_session_date
  ORDER BY ts DESC, price_id DESC LIMIT 1
$$;

CREATE OR REPLACE FUNCTION public.sp_alert_observe_threshold(p_rule_id uuid, p_instrument_id uuid,
  p_observed_at timestamptz, p_matched boolean, p_state_key text) RETURNS bigint
LANGUAGE plpgsql AS $$
DECLARE s public.alert_threshold_state%ROWTYPE;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_rule_id::text || ':' || p_instrument_id::text, 0));
  SELECT * INTO s FROM public.alert_threshold_state
  WHERE rule_id=p_rule_id AND instrument_id=p_instrument_id FOR UPDATE;
  IF FOUND AND p_observed_at<s.observed_at THEN RETURN NULL; END IF;
  INSERT INTO public.alert_threshold_state AS current (rule_id,instrument_id,observed_at,matched,state_key)
  VALUES(p_rule_id,p_instrument_id,p_observed_at,p_matched,p_state_key)
  ON CONFLICT(rule_id,instrument_id) DO UPDATE SET
    observed_at=excluded.observed_at, matched=excluded.matched, state_key=excluded.state_key,
    generation=current.generation + CASE WHEN current.state_key<>excluded.state_key
      OR (NOT current.matched AND excluded.matched) THEN 1 ELSE 0 END
  RETURNING * INTO s;
  IF NOT s.matched THEN RETURN NULL; END IF;
  RETURN s.generation;
END $$;

CREATE OR REPLACE FUNCTION public.sp_alert_register_match(p_rule_id uuid, p_instrument_id uuid, p_signal_id uuid, p_fingerprint character varying, p_payload jsonb) RETURNS uuid
LANGUAGE plpgsql AS $$
DECLARE r public.alert_rules%ROWTYPE; e public.alert_events%ROWTYPE; v uuid;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(p_rule_id::text || ':' || p_instrument_id::text, 0));
  SELECT * INTO r FROM public.alert_rules WHERE rule_id=p_rule_id AND enabled FOR SHARE;
  IF NOT FOUND THEN RETURN NULL; END IF;
  SELECT * INTO e FROM public.alert_events WHERE rule_id=p_rule_id AND instrument_id=p_instrument_id ORDER BY created_at DESC LIMIT 1;
  IF FOUND AND (e.fingerprint=p_fingerprint OR e.created_at>now()-make_interval(mins=>r.cooldown_minutes)) THEN RETURN NULL; END IF;
  IF coalesce((p_payload->>'once_per_session')::boolean, false)
     AND p_payload->>'session_key' IS NOT NULL AND EXISTS (
    SELECT 1 FROM public.alert_events a
    WHERE a.rule_id=p_rule_id AND a.instrument_id=p_instrument_id
      AND a.payload->>'session_key'=p_payload->>'session_key'
      AND a.payload->>'direction_key' IS NOT DISTINCT FROM p_payload->>'direction_key'
  ) THEN RETURN NULL; END IF;
  INSERT INTO public.alert_events(rule_id,user_id,instrument_id,signal_id,fingerprint,payload)
  VALUES(p_rule_id,r.user_id,p_instrument_id,p_signal_id,p_fingerprint,coalesce(p_payload,'{}'))
  ON CONFLICT(rule_id,instrument_id,fingerprint) DO NOTHING RETURNING alert_event_id INTO v;
  RETURN v;
END $$;

COMMIT;