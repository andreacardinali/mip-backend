DO $migration$
DECLARE
    function_definition text;
    metadata_statement text;
    source_delete_statement text;
BEGIN
    function_definition := pg_get_functiondef(
        'public.sp_consolidate_instrument(uuid,uuid,boolean,boolean)'::regprocedure
    );

    function_definition := replace(
        function_definition,
        'ON CONFLICT (alias_symbol) DO NOTHING',
        $conflict$ON CONFLICT (instrument_id, upper(btrim((alias_symbol)::text))) WHERE ((instrument_id IS NOT NULL) AND (NULLIF(btrim((alias_symbol)::text), ''::text) IS NOT NULL)) DO NOTHING$conflict$
    );

    metadata_statement := substring(
        function_definition FROM
        $metadata$(?s)    /\*\s+\* Fill only missing target metadata\..*?\*/\s+UPDATE instruments\s+SET\s+exchange = COALESCE\(.*?WHERE instrument_id = p_target_instrument_id;$metadata$
    );
    source_delete_statement := substring(
        function_definition FROM
        $source_delete$(?s)    DELETE FROM instruments\s+WHERE instrument_id = p_source_instrument_id;$source_delete$
    );

    IF metadata_statement IS NULL OR source_delete_statement IS NULL THEN
        RAISE EXCEPTION 'Unexpected consolidation function body; no changes applied';
    END IF;

    IF strpos(function_definition, metadata_statement)
       < strpos(function_definition, source_delete_statement)
    THEN
        function_definition := replace(function_definition, metadata_statement, '');
        function_definition := replace(
            function_definition,
            source_delete_statement,
            source_delete_statement || E'\n\n' || metadata_statement
        );
    END IF;

    EXECUTE function_definition;
END;
$migration$;