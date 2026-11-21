-- Marketplace schema repair for production.
-- This migration is intentionally additive: existing creator products/listings are preserved.
CREATE TABLE IF NOT EXISTS public.creator_promotion_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  listing_id uuid NOT NULL REFERENCES public.creator_marketplace_listings(id) ON DELETE CASCADE,
  product_id uuid NOT NULL REFERENCES public.creator_products(id) ON DELETE CASCADE,
  owner_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  promoter_id uuid NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','approved','rejected','withdrawn')),
  message text NOT NULL DEFAULT '' CHECK (char_length(message) <= 2000),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_creator_promotion_requests_listing ON public.creator_promotion_requests(listing_id);
CREATE INDEX IF NOT EXISTS idx_creator_promotion_requests_product ON public.creator_promotion_requests(product_id);
CREATE INDEX IF NOT EXISTS idx_creator_promotion_requests_owner_created ON public.creator_promotion_requests(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_creator_promotion_requests_promoter_created ON public.creator_promotion_requests(promoter_id, created_at DESC);
ALTER TABLE public.creator_promotion_requests ENABLE ROW LEVEL SECURITY;
GRANT SELECT, INSERT, UPDATE ON public.creator_promotion_requests TO authenticated;
GRANT ALL ON public.creator_promotion_requests TO service_role;

DROP POLICY IF EXISTS "participants read promotion requests" ON public.creator_promotion_requests;
CREATE POLICY "participants read promotion requests" ON public.creator_promotion_requests FOR SELECT TO authenticated
USING (promoter_id = (SELECT auth.uid()) OR owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS "promoters create own promotion requests" ON public.creator_promotion_requests;
CREATE POLICY "promoters create own promotion requests" ON public.creator_promotion_requests FOR INSERT TO authenticated
WITH CHECK (
  promoter_id = (SELECT auth.uid()) AND status = 'pending' AND EXISTS (
    SELECT 1 FROM public.creator_marketplace_listings l
    JOIN public.creator_products p ON p.id = l.product_id
    WHERE l.id = creator_promotion_requests.listing_id AND l.status = 'approved'
      AND p.status = 'listed' AND p.marketplace_enabled = true
      AND p.owner_id = creator_promotion_requests.owner_id AND p.owner_id <> (SELECT auth.uid())
  )
);
DROP POLICY IF EXISTS "promoters withdraw own promotion requests" ON public.creator_promotion_requests;
CREATE POLICY "promoters withdraw own promotion requests" ON public.creator_promotion_requests FOR UPDATE TO authenticated
USING (promoter_id = (SELECT auth.uid()))
WITH CHECK (promoter_id = (SELECT auth.uid()) AND status = 'withdrawn');
DROP POLICY IF EXISTS "vendors review promotion requests" ON public.creator_promotion_requests;
CREATE POLICY "vendors review promotion requests" ON public.creator_promotion_requests FOR UPDATE TO authenticated
USING (owner_id = (SELECT auth.uid()))
WITH CHECK (owner_id = (SELECT auth.uid()) AND status IN ('approved','rejected','pending'));

CREATE TABLE IF NOT EXISTS public.aea_marketplace_admins (
  user_id uuid PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
  active boolean NOT NULL DEFAULT true,
  created_at timestamptz NOT NULL DEFAULT now()
);
ALTER TABLE public.aea_marketplace_admins ENABLE ROW LEVEL SECURITY;
GRANT SELECT ON public.aea_marketplace_admins TO authenticated;
GRANT ALL ON public.aea_marketplace_admins TO service_role;
DROP POLICY IF EXISTS "admins read own marketplace role" ON public.aea_marketplace_admins;
CREATE POLICY "admins read own marketplace role" ON public.aea_marketplace_admins FOR SELECT TO authenticated
USING (user_id = (SELECT auth.uid()));

DROP POLICY IF EXISTS "admins read marketplace listings" ON public.creator_marketplace_listings;
CREATE POLICY "admins read marketplace listings" ON public.creator_marketplace_listings FOR SELECT TO authenticated
USING (EXISTS (SELECT 1 FROM public.aea_marketplace_admins a WHERE a.user_id = (SELECT auth.uid()) AND a.active = true));
DROP POLICY IF EXISTS "public browse approved marketplace listings" ON public.creator_marketplace_listings;
CREATE POLICY "public browse approved marketplace listings" ON public.creator_marketplace_listings FOR SELECT TO authenticated
USING (status = 'approved');
DROP POLICY IF EXISTS "vendors create own marketplace listing requests" ON public.creator_marketplace_listings;
CREATE POLICY "vendors create own marketplace listing requests" ON public.creator_marketplace_listings FOR INSERT TO authenticated
WITH CHECK (
  owner_id = (SELECT auth.uid()) AND status = 'pending' AND EXISTS (
    SELECT 1 FROM public.creator_products p WHERE p.id = creator_marketplace_listings.product_id
      AND p.owner_id = (SELECT auth.uid()) AND p.status = 'listed' AND p.marketplace_enabled = true
  )
);
DROP POLICY IF EXISTS "vendors read own marketplace listings" ON public.creator_marketplace_listings;
CREATE POLICY "vendors read own marketplace listings" ON public.creator_marketplace_listings FOR SELECT TO authenticated
USING (owner_id = (SELECT auth.uid()));
DROP POLICY IF EXISTS "vendors update own pending marketplace listings" ON public.creator_marketplace_listings;
CREATE POLICY "vendors update own pending marketplace listings" ON public.creator_marketplace_listings FOR UPDATE TO authenticated
USING (owner_id = (SELECT auth.uid()) AND status = 'pending')
WITH CHECK (owner_id = (SELECT auth.uid()) AND status = 'pending');
DROP POLICY IF EXISTS "vendors delete own pending marketplace listings" ON public.creator_marketplace_listings;
CREATE POLICY "vendors delete own pending marketplace listings" ON public.creator_marketplace_listings FOR DELETE TO authenticated
USING (owner_id = (SELECT auth.uid()) AND status = 'pending');
DROP POLICY IF EXISTS "admins review marketplace listings" ON public.creator_marketplace_listings;
CREATE POLICY "admins review marketplace listings" ON public.creator_marketplace_listings FOR UPDATE TO authenticated
USING (EXISTS (SELECT 1 FROM public.aea_marketplace_admins a WHERE a.user_id = (SELECT auth.uid()) AND a.active = true))
WITH CHECK (EXISTS (SELECT 1 FROM public.aea_marketplace_admins a WHERE a.user_id = (SELECT auth.uid()) AND a.active = true));

-- Authenticated users may change only the request status, never ownership or identities.
REVOKE UPDATE ON TABLE public.creator_promotion_requests FROM authenticated;
GRANT UPDATE (status) ON TABLE public.creator_promotion_requests TO authenticated;

-- Seed the already-verified production Auth user selected by the account owner.
INSERT INTO public.aea_marketplace_admins (user_id, active)
VALUES ('e1e4e8c7-5f57-47bd-8d6c-186ce1d4afb7', true)
ON CONFLICT (user_id) DO UPDATE SET active = true;
