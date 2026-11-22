-- Durable distribution drafts and approval-gated publishing lifecycle.
-- A draft is a request only; it is not evidence of publication.
CREATE TABLE IF NOT EXISTS public.creator_distribution_jobs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    product_id uuid NOT NULL REFERENCES public.creator_products(id) ON DELETE CASCADE,
    owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    destination text NOT NULL CHECK (destination IN ('pinterest','facebook','instagram','tiktok','youtube')),
    status text NOT NULL DEFAULT 'draft'
        CHECK (status IN ('draft','pending_approval','approved','running','published','failed','cancelled')),
    approval_required boolean NOT NULL DEFAULT true,
    approval_request_id uuid REFERENCES public.approval_requests(id) ON DELETE SET NULL,
    error text,
    provider_reference text,
    provider_url text,
    published_at timestamptz,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
);

-- At most one active draft per product/platform/owner, even under concurrent requests.
CREATE UNIQUE INDEX IF NOT EXISTS creator_distribution_jobs_one_draft_per_destination
    ON public.creator_distribution_jobs (product_id, owner_id, destination)
    WHERE status = 'draft';

CREATE INDEX IF NOT EXISTS creator_distribution_jobs_owner_created
    ON public.creator_distribution_jobs (owner_id, created_at DESC);

ALTER TABLE public.creator_distribution_jobs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "owners read own distribution jobs" ON public.creator_distribution_jobs;
CREATE POLICY "owners read own distribution jobs"
    ON public.creator_distribution_jobs FOR SELECT TO authenticated
    USING ((SELECT auth.uid()) = owner_id);

DROP POLICY IF EXISTS "owners create own distribution drafts" ON public.creator_distribution_jobs;
CREATE POLICY "owners create own distribution drafts"
    ON public.creator_distribution_jobs FOR INSERT TO authenticated
    WITH CHECK (
        (SELECT auth.uid()) = owner_id
        AND status = 'draft'
        AND approval_required = true
        AND approval_request_id IS NULL
        AND provider_reference IS NULL
        AND published_at IS NULL
        AND EXISTS (
            SELECT 1 FROM public.creator_products p
            WHERE p.id = creator_distribution_jobs.product_id
              AND p.owner_id = (SELECT auth.uid())
        )
    );

-- Browser clients can only create/read drafts. Lifecycle transitions and provider
-- evidence are written by the trusted server using service-role access.
REVOKE ALL ON public.creator_distribution_jobs FROM anon, authenticated;
GRANT SELECT, INSERT ON public.creator_distribution_jobs TO authenticated;
GRANT ALL ON public.creator_distribution_jobs TO service_role;
