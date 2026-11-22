-- Durable distribution drafts and approval-gated publishing lifecycle.
-- This migration is intentionally additive: the Hub may already have created
-- creator_distribution_jobs with legacy external_id/external_url/scheduled_at
-- columns. Preserve those records and columns while adding the Core lifecycle.

CREATE TABLE IF NOT EXISTS public.creator_distribution_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id uuid NOT NULL REFERENCES public.creator_products(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    destination text NOT NULL,
    status text NOT NULL DEFAULT 'draft',
    approval_required boolean NOT NULL DEFAULT true,
    approval_request_id uuid,
    error text,
    provider_reference text,
    provider_url text,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- Bring forward existing Hub-created tables without destructive replacement.
ALTER TABLE public.creator_distribution_jobs
    ADD COLUMN IF NOT EXISTS approval_request_id uuid,
    ADD COLUMN IF NOT EXISTS provider_reference text,
    ADD COLUMN IF NOT EXISTS provider_url text,
    ADD COLUMN IF NOT EXISTS published_at timestamptz;

-- Keep the original Hub marketplace destination and scheduled state valid.
ALTER TABLE public.creator_distribution_jobs
    DROP CONSTRAINT IF EXISTS creator_distribution_jobs_destination_check;
ALTER TABLE public.creator_distribution_jobs
    ADD CONSTRAINT creator_distribution_jobs_destination_check
    CHECK (destination IN (
        'pinterest','facebook','instagram','tiktok','youtube','affiliate_marketplace'
    ));

ALTER TABLE public.creator_distribution_jobs
    DROP CONSTRAINT IF EXISTS creator_distribution_jobs_status_check;
ALTER TABLE public.creator_distribution_jobs
    ADD CONSTRAINT creator_distribution_jobs_status_check
    CHECK (status IN (
        'draft','pending_approval','approved','running','scheduled',
        'published','failed','cancelled'
    ));

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'creator_distribution_jobs_approval_request_id_fkey'
          AND conrelid = 'public.creator_distribution_jobs'::regclass
    ) THEN
        ALTER TABLE public.creator_distribution_jobs
            ADD CONSTRAINT creator_distribution_jobs_approval_request_id_fkey
            FOREIGN KEY (approval_request_id)
            REFERENCES public.approval_requests(id)
            ON DELETE SET NULL;
    END IF;
END $$;

-- At most one active draft per product/platform/owner, including concurrent requests.
CREATE UNIQUE INDEX IF NOT EXISTS creator_distribution_jobs_one_draft_per_destination
    ON public.creator_distribution_jobs (product_id, owner_id, destination)
    WHERE status = 'draft';

CREATE INDEX IF NOT EXISTS creator_distribution_jobs_owner_created
    ON public.creator_distribution_jobs (owner_id, created_at DESC);

ALTER TABLE public.creator_distribution_jobs ENABLE ROW LEVEL SECURITY;

-- Replace the earlier broad owner-management policy. Browser users may read
-- their own jobs and create constrained drafts, but cannot approve or publish.
DROP POLICY IF EXISTS "owners manage own distribution jobs" ON public.creator_distribution_jobs;
DROP POLICY IF EXISTS "owners read own distribution jobs" ON public.creator_distribution_jobs;
DROP POLICY IF EXISTS "owners create own distribution drafts" ON public.creator_distribution_jobs;

CREATE POLICY "owners read own distribution jobs"
    ON public.creator_distribution_jobs FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = owner_id);

CREATE POLICY "owners create own distribution drafts"
    ON public.creator_distribution_jobs FOR INSERT TO authenticated
    WITH CHECK (
        (SELECT auth.uid()) = owner_id
        AND status = 'draft'
        AND approval_required = true
        AND approval_request_id IS NULL
        AND provider_reference IS NULL
        AND provider_url IS NULL
        AND published_at IS NULL
        AND EXISTS (
            SELECT 1 FROM public.creator_products p
            WHERE p.id = creator_distribution_jobs.product_id
              AND p.owner_id = (SELECT auth.uid())
        )
    );

-- Only the trusted Core service may perform lifecycle transitions or record
-- provider publication evidence. RLS remains enabled as defense in depth.
REVOKE ALL ON public.creator_distribution_jobs FROM anon, authenticated;
GRANT SELECT, INSERT ON public.creator_distribution_jobs TO authenticated;
GRANT ALL ON public.creator_distribution_jobs TO service_role;
