-- Public catalogue is opt-in and only shows vendor-approved marketplace listings.
alter table public.creator_products
  add column if not exists marketplace_enabled boolean not null default false;

create policy "public browse approved marketplace listings"
on public.creator_marketplace_listings
for select
to authenticated
using (
  status = 'approved'
  and exists (
    select 1 from public.creator_products p
    where p.id = creator_marketplace_listings.product_id
      and p.status = 'listed'
      and p.marketplace_enabled = true
  )
);

create table if not exists public.creator_promotion_requests (
  id uuid primary key default gen_random_uuid(),
  listing_id uuid not null references public.creator_marketplace_listings(id) on delete cascade,
  product_id uuid not null references public.creator_products(id) on delete cascade,
  owner_id uuid not null references auth.users(id) on delete cascade,
  promoter_id uuid not null references auth.users(id) on delete cascade,
  status text not null default 'pending'
    check (status in ('pending','approved','rejected','withdrawn')),
  message text not null default '' check (char_length(message) <= 2000),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (listing_id, promoter_id),
  check (owner_id <> promoter_id)
);

create index if not exists creator_promotion_requests_owner_status_idx
  on public.creator_promotion_requests(owner_id, status, created_at desc);
create index if not exists creator_promotion_requests_promoter_idx
  on public.creator_promotion_requests(promoter_id, created_at desc);

alter table public.creator_promotion_requests enable row level security;

create policy "promoters create own promotion requests"
on public.creator_promotion_requests
for insert to authenticated
with check (
  promoter_id = (select auth.uid())
  and exists (
    select 1 from public.creator_marketplace_listings l
    join public.creator_products p on p.id = l.product_id
    where l.id = listing_id
      and l.status = 'approved'
      and p.status = 'listed'
      and p.marketplace_enabled = true
      and p.owner_id = creator_promotion_requests.owner_id
      and p.owner_id <> (select auth.uid())
  )
);

create policy "participants read promotion requests"
on public.creator_promotion_requests
for select to authenticated
using (
  promoter_id = (select auth.uid())
  or owner_id = (select auth.uid())
);

create policy "vendors review promotion requests"
on public.creator_promotion_requests
for update to authenticated
using (owner_id = (select auth.uid()))
with check (
  owner_id = (select auth.uid())
  and status in ('approved','rejected','pending')
);

create policy "promoters withdraw own promotion requests"
on public.creator_promotion_requests
for update to authenticated
using (promoter_id = (select auth.uid()))
with check (
  promoter_id = (select auth.uid())
  and status = 'withdrawn'
);
