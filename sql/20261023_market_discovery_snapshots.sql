BEGIN;

CREATE TABLE IF NOT EXISTS public.market_discovery_sources (
    source_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    provider_code varchar(50) NOT NULL,
    source_type varchar(50) NOT NULL,
    name varchar(255) NOT NULL,
    is_enabled boolean DEFAULT true NOT NULL,
    created_at timestamptz DEFAULT now() NOT NULL,
    updated_at timestamptz DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_discovery_sources_provider CHECK (btrim(provider_code) <> ''),
    CONSTRAINT ck_market_discovery_sources_type CHECK (btrim(source_type) <> ''),
    CONSTRAINT ck_market_discovery_sources_name CHECK (btrim(name) <> ''),
    CONSTRAINT uq_market_discovery_sources UNIQUE (provider_code, source_type)
);

CREATE TABLE IF NOT EXISTS public.market_discovery_snapshots (
    snapshot_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    source_id uuid NOT NULL REFERENCES public.market_discovery_sources(source_id) ON DELETE RESTRICT,
    observed_at timestamptz NOT NULL,
    requested_limit integer NOT NULL,
    returned_count integer NOT NULL,
    source_reference text,
    source_metadata jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamptz DEFAULT now() NOT NULL,
    updated_at timestamptz DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_discovery_snapshots_limit CHECK (requested_limit > 0),
    CONSTRAINT ck_market_discovery_snapshots_count CHECK (returned_count >= 0)
);

CREATE TABLE IF NOT EXISTS public.market_discovery_snapshot_items (
    snapshot_item_id uuid DEFAULT gen_random_uuid() PRIMARY KEY,
    snapshot_id uuid NOT NULL REFERENCES public.market_discovery_snapshots(snapshot_id) ON DELETE CASCADE,
    symbol varchar(50) NOT NULL,
    name varchar(255),
    rank integer,
    previous_rank integer,
    mentions integer,
    previous_mentions integer,
    upvotes integer,
    score numeric,
    raw_payload jsonb DEFAULT '{}'::jsonb NOT NULL,
    created_at timestamptz DEFAULT now() NOT NULL,
    updated_at timestamptz DEFAULT now() NOT NULL,
    CONSTRAINT ck_market_discovery_items_symbol CHECK (btrim(symbol) <> ''),
    CONSTRAINT ck_market_discovery_items_rank CHECK (rank IS NULL OR rank > 0),
    CONSTRAINT ck_market_discovery_items_previous_rank CHECK (previous_rank IS NULL OR previous_rank > 0),
    CONSTRAINT ck_market_discovery_items_mentions CHECK (mentions IS NULL OR mentions >= 0),
    CONSTRAINT ck_market_discovery_items_previous_mentions CHECK (previous_mentions IS NULL OR previous_mentions >= 0),
    CONSTRAINT ck_market_discovery_items_upvotes CHECK (upvotes IS NULL OR upvotes >= 0),
    CONSTRAINT uq_market_discovery_items_symbol UNIQUE (snapshot_id, symbol),
    CONSTRAINT uq_market_discovery_items_rank UNIQUE (snapshot_id, rank)
);

CREATE INDEX IF NOT EXISTS ix_market_discovery_snapshots_latest
    ON public.market_discovery_snapshots(source_id, observed_at DESC, snapshot_id DESC);
CREATE INDEX IF NOT EXISTS ix_market_discovery_snapshot_items_symbol
    ON public.market_discovery_snapshot_items(symbol, snapshot_id);

DROP TRIGGER IF EXISTS trg_market_discovery_sources_created_at ON public.market_discovery_sources;
CREATE TRIGGER trg_market_discovery_sources_created_at
BEFORE INSERT ON public.market_discovery_sources
FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();
DROP TRIGGER IF EXISTS trg_market_discovery_sources_updated_at ON public.market_discovery_sources;
CREATE TRIGGER trg_market_discovery_sources_updated_at
BEFORE INSERT OR UPDATE ON public.market_discovery_sources
FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();

DROP TRIGGER IF EXISTS trg_market_discovery_snapshots_created_at ON public.market_discovery_snapshots;
CREATE TRIGGER trg_market_discovery_snapshots_created_at
BEFORE INSERT ON public.market_discovery_snapshots
FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();
DROP TRIGGER IF EXISTS trg_market_discovery_snapshots_updated_at ON public.market_discovery_snapshots;
CREATE TRIGGER trg_market_discovery_snapshots_updated_at
BEFORE INSERT OR UPDATE ON public.market_discovery_snapshots
FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();

DROP TRIGGER IF EXISTS trg_market_discovery_snapshot_items_created_at ON public.market_discovery_snapshot_items;
CREATE TRIGGER trg_market_discovery_snapshot_items_created_at
BEFORE INSERT ON public.market_discovery_snapshot_items
FOR EACH ROW EXECUTE FUNCTION public.trg_set_created_at();
DROP TRIGGER IF EXISTS trg_market_discovery_snapshot_items_updated_at ON public.market_discovery_snapshot_items;
CREATE TRIGGER trg_market_discovery_snapshot_items_updated_at
BEFORE INSERT OR UPDATE ON public.market_discovery_snapshot_items
FOR EACH ROW EXECUTE FUNCTION public.trg_set_updated_at();

CREATE OR REPLACE FUNCTION public.sp_market_discovery_upsert_source(
    p_provider_code varchar,
    p_source_type varchar,
    p_name varchar,
    p_is_enabled boolean DEFAULT true
) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE v_source_id uuid;
BEGIN
    IF NULLIF(btrim(p_provider_code), '') IS NULL OR NULLIF(btrim(p_source_type), '') IS NULL THEN
        RAISE EXCEPTION 'provider_code and source_type are required';
    END IF;
    INSERT INTO public.market_discovery_sources(provider_code, source_type, name, is_enabled)
    VALUES (upper(btrim(p_provider_code)), upper(btrim(p_source_type)), btrim(p_name), p_is_enabled)
    ON CONFLICT (provider_code, source_type) DO UPDATE SET
        name = EXCLUDED.name,
        is_enabled = EXCLUDED.is_enabled
    RETURNING source_id INTO v_source_id;
    RETURN v_source_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_discovery_create_snapshot(
    p_provider_code varchar,
    p_source_type varchar,
    p_source_name varchar,
    p_observed_at timestamptz,
    p_requested_limit integer,
    p_source_reference text,
    p_source_metadata jsonb,
    p_items jsonb
) RETURNS uuid LANGUAGE plpgsql AS $$
DECLARE
    v_source_id uuid;
    v_snapshot_id uuid;
    v_item_count integer;
BEGIN
    IF p_observed_at IS NULL THEN RAISE EXCEPTION 'observed_at is required'; END IF;
    IF p_requested_limit < 1 THEN RAISE EXCEPTION 'requested_limit must be positive'; END IF;
    IF p_items IS NULL OR jsonb_typeof(p_items) <> 'array' THEN RAISE EXCEPTION 'items must be a JSON array'; END IF;
    SELECT count(*) INTO v_item_count FROM jsonb_array_elements(p_items);
    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_items) AS x(symbol text)
        WHERE NULLIF(btrim(x.symbol), '') IS NULL
    ) THEN RAISE EXCEPTION 'every item requires a symbol'; END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_items) AS x(symbol text)
        GROUP BY upper(btrim(x.symbol)) HAVING count(*) > 1
    ) THEN RAISE EXCEPTION 'items contain duplicate symbols'; END IF;
    IF EXISTS (
        SELECT 1 FROM jsonb_to_recordset(p_items) AS x(rank integer)
        WHERE x.rank IS NOT NULL GROUP BY x.rank HAVING count(*) > 1
    ) THEN RAISE EXCEPTION 'items contain duplicate ranks'; END IF;

    v_source_id := public.sp_market_discovery_upsert_source(
        p_provider_code, p_source_type, p_source_name, true
    );
    INSERT INTO public.market_discovery_snapshots(
        source_id, observed_at, requested_limit, returned_count, source_reference, source_metadata
    ) VALUES (
        v_source_id, p_observed_at, p_requested_limit, v_item_count,
        NULLIF(btrim(p_source_reference), ''), COALESCE(p_source_metadata, '{}'::jsonb)
    ) RETURNING snapshot_id INTO v_snapshot_id;

    INSERT INTO public.market_discovery_snapshot_items(
        snapshot_id, symbol, name, rank, previous_rank, mentions,
        previous_mentions, upvotes, score, raw_payload
    )
    SELECT v_snapshot_id, upper(btrim(x.symbol)), NULLIF(btrim(x.name), ''),
           x.rank, x.previous_rank, x.mentions, x.previous_mentions,
           x.upvotes, x.score, COALESCE(x.raw_payload, '{}'::jsonb)
    FROM jsonb_to_recordset(p_items) AS x(
        symbol text, name text, rank integer, previous_rank integer,
        mentions integer, previous_mentions integer, upvotes integer,
        score numeric, raw_payload jsonb
    );
    RETURN v_snapshot_id;
END;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_discovery_get_latest_snapshot(
    p_provider_code varchar,
    p_source_type varchar
) RETURNS jsonb LANGUAGE sql STABLE AS $$
SELECT jsonb_build_object(
    'snapshot_id', s.snapshot_id, 'provider_code', src.provider_code,
    'source_type', src.source_type, 'source_name', src.name,
    'observed_at', s.observed_at, 'requested_limit', s.requested_limit,
    'returned_count', s.returned_count, 'source_reference', s.source_reference,
    'source_metadata', s.source_metadata,
    'items', COALESCE((SELECT jsonb_agg(to_jsonb(i) - 'snapshot_id' - 'created_at' - 'updated_at'
        ORDER BY i.rank NULLS LAST, i.symbol) FROM public.market_discovery_snapshot_items i
        WHERE i.snapshot_id = s.snapshot_id), '[]'::jsonb)
)
FROM public.market_discovery_snapshots s
JOIN public.market_discovery_sources src ON src.source_id = s.source_id
WHERE src.provider_code = upper(btrim(p_provider_code))
  AND src.source_type = upper(btrim(p_source_type))
ORDER BY s.observed_at DESC, s.snapshot_id DESC LIMIT 1;
$$;

CREATE OR REPLACE FUNCTION public.sp_market_discovery_list_snapshot_history(
    p_provider_code varchar,
    p_source_type varchar,
    p_limit integer DEFAULT 100
) RETURNS TABLE(snapshot_id uuid, observed_at timestamptz, requested_limit integer,
returned_count integer, source_reference text, source_metadata jsonb)
LANGUAGE sql STABLE AS $$
SELECT s.snapshot_id, s.observed_at, s.requested_limit, s.returned_count,
       s.source_reference, s.source_metadata
FROM public.market_discovery_snapshots s
JOIN public.market_discovery_sources src ON src.source_id = s.source_id
WHERE src.provider_code = upper(btrim(p_provider_code))
  AND src.source_type = upper(btrim(p_source_type))
ORDER BY s.observed_at DESC, s.snapshot_id DESC
LIMIT least(greatest(p_limit, 1), 1000);
$$;

CREATE OR REPLACE FUNCTION public.sp_market_discovery_delete_expired_snapshots(
    p_retention_days integer
) RETURNS integer LANGUAGE plpgsql AS $$
DECLARE v_deleted integer;
BEGIN
    IF p_retention_days < 1 THEN RAISE EXCEPTION 'retention_days must be positive'; END IF;
    DELETE FROM public.market_discovery_snapshots
    WHERE observed_at < now() - make_interval(days => p_retention_days);
    GET DIAGNOSTICS v_deleted = ROW_COUNT;
    RETURN v_deleted;
END;
$$;

COMMIT;
