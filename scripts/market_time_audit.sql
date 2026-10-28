BEGIN READ ONLY;

SHOW timezone;

SELECT table_name, column_name, data_type, column_default
FROM information_schema.columns
WHERE table_schema = 'public'
  AND data_type IN (
      'timestamp without time zone', 'timestamp with time zone',
      'date', 'time without time zone', 'time with time zone'
  )
ORDER BY table_name, ordinal_position;

SELECT e.exchange,
       to_jsonb(e)->>'timezone_name' AS timezone_name,
       to_jsonb(e)->>'calendar_name' AS calendar_name,
       to_jsonb(e)->>'market_open_time' AS market_open_time,
       to_jsonb(e)->>'market_close_time' AS market_close_time
FROM public.exchanges e
ORDER BY e.exchange;

SELECT p.oid::regprocedure AS signature,
       pg_get_userbyid(p.proowner) AS owner,
       p.proacl AS grants
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
WHERE n.nspname = 'public'
  AND p.proname = 'sp_upsert_filing';

SELECT p.oid::regprocedure AS signature,
       pg_describe_object(d.classid, d.objid, d.objsubid) AS dependent_object,
       d.deptype
FROM pg_proc p
JOIN pg_namespace n ON n.oid = p.pronamespace
JOIN pg_depend d ON d.refclassid = 'pg_proc'::regclass AND d.refobjid = p.oid
WHERE n.nspname = 'public'
  AND p.proname = 'sp_upsert_filing';

COMMIT;