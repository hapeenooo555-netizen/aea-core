-- Harden Marketplace writes against direct PostgREST/Supabase table access.
-- Keep row ownership in RLS and restrict mutable columns at the SQL privilege layer.

-- Promotion requests may only be changed through their status field.
REVOKE UPDATE ON TABLE public.creator_promotion_requests FROM authenticated;
REVOKE UPDATE (
    id, listing_id, product_id, owner_id, promoter_id, status, message, created_at, updated_at
) ON TABLE public.creator_promotion_requests FROM authenticated;
GRANT UPDATE (status) ON TABLE public.creator_promotion_requests TO authenticated;

-- Listing identity and ownership fields are immutable to authenticated clients.
-- Vendors may update price/currency while their listing is pending; Admin review
-- changes only status. Existing RLS policies continue to decide which rows each
-- user may update.
REVOKE UPDATE ON TABLE public.creator_marketplace_listings FROM authenticated;
REVOKE UPDATE (
    id, product_id, owner_id, status, price_amount, currency, created_at, updated_at
) ON TABLE public.creator_marketplace_listings FROM authenticated;
GRANT UPDATE (status, price_amount, currency)
    ON TABLE public.creator_marketplace_listings TO authenticated;

-- A promoter cannot insert a request that is already approved/rejected/withdrawn.
DROP POLICY IF EXISTS "promoters create own promotion requests"
    ON public.creator_promotion_requests;
CREATE POLICY "promoters create own promotion requests"
    ON public.creator_promotion_requests
    FOR INSERT TO authenticated
    WITH CHECK (
        promoter_id = (SELECT auth.uid())
        AND status = 'pending'
        AND EXISTS (
            SELECT 1
            FROM public.creator_marketplace_listings l
            JOIN public.creator_products p ON p.id = l.product_id
            WHERE l.id = creator_promotion_requests.listing_id
              AND l.status = 'approved'
              AND p.status = 'listed'
              AND p.marketplace_enabled = true
              AND p.owner_id = creator_promotion_requests.owner_id
              AND p.owner_id <> (SELECT auth.uid())
        )
    );
