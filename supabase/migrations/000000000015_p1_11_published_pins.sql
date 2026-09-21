-- P1-11: Durable pin publishing persistence with RLS and idempotency.
--
-- Creates public.published_pins to record content published through platform
-- connectors. The operation_key column enforces database-authoritative
-- idempotency: at most one row per operation_key, surviving process restarts
-- and cross-process concurrency.
--
-- All access is owner-scoped via owner_id = auth.uid(). The anon role is
-- revoked; only authenticated may perform DML.
--
-- Prerequisite: approval_requests table must already exist (migration 004)
-- and P1-4C RLS hardening (migration 009) must have added owner_id UUID to
-- approval_requests, since operation_key is derived from approval_request_id.
BEGIN;

CREATE TABLE IF NOT EXISTS public.published_pins (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    owner_id             UUID NOT NULL,
    worker_id            TEXT NOT NULL,
    platform             TEXT NOT NULL,
    operation_key        TEXT NOT NULL,
    approval_request_id  UUID,
    board_name           TEXT NOT NULL,
    pin_text             TEXT NOT NULL,
    link_url             TEXT NOT NULL,
    pin_id               TEXT,
    status               TEXT NOT NULL DEFAULT 'published',
    content              JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.published_pins IS 'Records of content published via platform connectors. Source of truth for publish idempotency.';
COMMENT ON COLUMN public.published_pins.owner_id IS 'Canonical owner (auth.users.id). RLS enforces isolation.';
COMMENT ON COLUMN public.published_pins.operation_key IS 'Server-derived key (p1-11:{approval_request_id}); deterministic idempotency boundary.';
COMMENT ON COLUMN public.published_pins.approval_request_id IS 'Originating approval request identifier.';
COMMENT ON COLUMN public.published_pins.content IS 'Non-sensitive content snapshot (sanitized). Never contains OAuth secrets or credentials.';
COMMENT ON COLUMN public.published_pins.pin_id IS 'Platform-assigned pin identifier (nullable until confirmed by the platform API).';

-- Database-authoritative idempotency: one pin per operation_key.
CREATE UNIQUE INDEX IF NOT EXISTS ux_published_pins_operation_key
    ON public.published_pins (operation_key);

-- Lookup indexes.
CREATE INDEX IF NOT EXISTS idx_published_pins_owner_id
    ON public.published_pins (owner_id);
CREATE INDEX IF NOT EXISTS idx_published_pins_approval_request_id
    ON public.published_pins (approval_request_id);
CREATE INDEX IF NOT EXISTS idx_published_pins_platform
    ON public.published_pins (platform);
CREATE INDEX IF NOT EXISTS idx_published_pins_created_at_desc
    ON public.published_pins (created_at DESC);

-- RLS
ALTER TABLE public.published_pins ENABLE ROW LEVEL SECURITY;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'published_pins'
          AND policyname = 'published_pins_select_own'
    ) THEN
        CREATE POLICY published_pins_select_own ON public.published_pins FOR SELECT
            USING (owner_id = auth.uid());
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'published_pins'
          AND policyname = 'published_pins_insert_own'
    ) THEN
        CREATE POLICY published_pins_insert_own ON public.published_pins FOR INSERT
            WITH CHECK (owner_id = auth.uid());
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'published_pins'
          AND policyname = 'published_pins_update_own'
    ) THEN
        CREATE POLICY published_pins_update_own ON public.published_pins FOR UPDATE
            USING (owner_id = auth.uid()) WITH CHECK (owner_id = auth.uid());
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'published_pins'
          AND policyname = 'published_pins_delete_own'
    ) THEN
        CREATE POLICY published_pins_delete_own ON public.published_pins FOR DELETE
            USING (owner_id = auth.uid());
    END IF;
END
$$;

-- Grants: anon gets nothing; authenticated gets DML.
REVOKE ALL ON public.published_pins FROM anon;
REVOKE ALL ON public.published_pins FROM PUBLIC;
GRANT SELECT, INSERT, UPDATE, DELETE ON public.published_pins TO authenticated;

COMMIT;
