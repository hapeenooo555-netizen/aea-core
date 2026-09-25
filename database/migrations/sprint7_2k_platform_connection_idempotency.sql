-- Sprint 7.2-G: Platform connection idempotency.
--
-- Adds a unique constraint on (owner_id, platform) for
-- platform_connections so that repeated connection attempts
-- do not create duplicate rows.
--
-- Before applying the constraint, a read-only duplicate check
-- is performed. If duplicates exist, the migration will fail
-- safely rather than silently destroying data.
--
-- Duplicate rows are NOT deleted, merged, or mutated.
BEGIN;

-- Read-only duplicate check. This does not modify any data.
-- If duplicates exist, the following query will return rows,
-- and the migration should be reviewed before proceeding.
-- The constraint below will fail if duplicates exist.
DO $$
DECLARE
    dup_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO dup_count
    FROM (
        SELECT owner_id, platform
        FROM public.platform_connections
        GROUP BY owner_id, platform
        HAVING COUNT(*) > 1
    ) dup;

    IF dup_count > 0 THEN
        RAISE WARNING 'Duplicate (owner_id, platform) rows exist in platform_connections. Review before applying constraint.';
    END IF;
END $$;

ALTER TABLE public.platform_connections
    ADD CONSTRAINT ux_platform_connections_owner_platform
    UNIQUE (owner_id, platform);

COMMENT ON CONSTRAINT ux_platform_connections_owner_platform ON public.platform_connections IS
    'Ensures at most one platform connection per owner per platform. Repeated connections use upsert semantics.';

COMMIT;
