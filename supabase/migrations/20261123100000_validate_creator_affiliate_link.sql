-- Read-only validation for AEA Marketplace referral links used by Pinterest Pins.
-- Unlike resolve_creator_affiliate_link(), this function never records a click.
create or replace function public.validate_creator_affiliate_link(
    p_code text,
    p_product_id uuid,
    p_promoter_id uuid
)
returns boolean
language sql
stable
security definer
set search_path = public, pg_temp
as $$
    select exists (
        select 1
          from public.creator_affiliate_links l
          join public.creator_products p on p.id = l.product_id
          join public.creator_marketplace_listings ml on ml.id = l.listing_id
          join public.creator_promotion_requests pr on pr.id = l.promotion_request_id
         where l.referral_code = p_code
           and l.product_id = p_product_id
           and l.promoter_id = p_promoter_id
           and l.status = 'active'
           and p.status = 'listed'
           and p.marketplace_enabled = true
           and ml.status = 'approved'
           and pr.status = 'approved'
           and pr.promoter_id = p_promoter_id
    );
$$;
revoke all on function public.validate_creator_affiliate_link(text, uuid, uuid) from public;
grant execute on function public.validate_creator_affiliate_link(text, uuid, uuid) to authenticated, service_role;
