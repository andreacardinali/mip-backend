BEGIN;

CREATE OR REPLACE FUNCTION public.sp_list_orphan_entities()
RETURNS TABLE(entity_id uuid)
LANGUAGE sql
AS $$
    SELECT e.entity_id
    FROM public.entities e
    WHERE NOT EXISTS (
        SELECT 1 FROM public.instruments i WHERE i.entity_id = e.entity_id
    )
    ORDER BY e.entity_id;
$$;

CREATE OR REPLACE FUNCTION public.sp_cleanup_orphan_entity(p_entity_id uuid)
RETURNS text
LANGUAGE plpgsql
AS $$
BEGIN
    PERFORM 1
    FROM public.entities e
    WHERE e.entity_id = p_entity_id
    FOR UPDATE SKIP LOCKED;

    IF NOT FOUND THEN
        RETURN 'skipped';
    END IF;

    DELETE FROM public.entities e
    WHERE e.entity_id = p_entity_id
      AND NOT EXISTS (
          SELECT 1 FROM public.instruments i WHERE i.entity_id = e.entity_id
      );

    IF FOUND THEN
        RETURN 'deleted';
    END IF;
    RETURN 'skipped';
EXCEPTION
    WHEN foreign_key_violation THEN
        RETURN 'referenced';
END;
$$;

COMMIT;