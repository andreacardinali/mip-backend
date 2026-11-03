BEGIN;

SELECT public.sp_register_exchange_alias('XNYS', NULL, 'NYSE');

COMMIT;