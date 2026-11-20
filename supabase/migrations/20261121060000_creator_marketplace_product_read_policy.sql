-- Permit authenticated users to read products only when their vendor has
-- explicitly enabled marketplace listing and the listing is approved.
drop policy if exists "read own or explicitly public creator products" on public.creator_products;
create policy "read own public community or approved marketplace products"
on public.creator_products
for select to authenticated
using (
  owner_id = (select auth.uid())
  or (
    visibility = 'community_public'
    and exists (
      select 1 from public.creator_profiles cp
      where cp.user_id = creator_products.owner_id and cp.is_public = true
    )
    and (
      age_rating = 'general'
      or (
        age_rating = '18_plus'
        and exists (
          select 1 from public.creator_age_verifications v
          where v.user_id = (select auth.uid()) and v.status = 'verified'
        )
      )
    )
  )
  or (
    marketplace_enabled = true
    and status = 'listed'
    and exists (
      select 1 from public.creator_marketplace_listings l
      join public.creator_profiles cp on cp.user_id = creator_products.owner_id
      where l.product_id = creator_products.id
        and l.status = 'approved'
        and cp.is_public = true
    )
    and (
      age_rating = 'general'
      or (
        age_rating = '18_plus'
        and exists (
          select 1 from public.creator_age_verifications v
          where v.user_id = (select auth.uid()) and v.status = 'verified'
        )
      )
    )
  )
);
