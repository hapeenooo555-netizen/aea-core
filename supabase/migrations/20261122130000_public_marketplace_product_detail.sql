-- Public product detail payload for AEA-hosted Marketplace pages.
-- Exposes only listed, marketplace-enabled products with an approved listing
-- and a public creator profile. No owner IDs, private metadata, or asset internals.
create or replace function public.get_public_creator_product_by_slug(p_slug text)
returns table (
  slug text,
  title text,
  description text,
  product_type text,
  price_amount numeric,
  currency text,
  age_rating text,
  vendor_display_name text
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select p.slug, p.title, p.description, p.product_type,
         coalesce(ml.price_amount, p.price_amount) as price_amount,
         coalesce(ml.currency, p.currency, 'TZS') as currency,
         p.age_rating, cp.display_name as vendor_display_name
    from public.creator_products p
    join public.creator_marketplace_listings ml
      on ml.product_id = p.id
     and ml.owner_id = p.owner_id
     and ml.status = 'approved'
    join public.creator_profiles cp
      on cp.user_id = p.owner_id
     and cp.is_public = true
   where p.slug = p_slug
     and p.status = 'listed'
     and p.marketplace_enabled = true
   limit 1;
$$;
revoke all on function public.get_public_creator_product_by_slug(text) from public;
grant execute on function public.get_public_creator_product_by_slug(text) to anon, authenticated;
