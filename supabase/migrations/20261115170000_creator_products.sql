-- Creator-owned product catalogue. Listings do not imply payment or delivery.
create table if not exists public.creator_products (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    asset_id uuid not null,
    title text not null check (char_length(title) between 3 and 180),
    slug text not null,
    description text not null check (char_length(description) between 20 and 5000),
    product_type text not null check (product_type in ('ebook','video','course','template','workbook','guide','digital_pack')),
    price_amount bigint not null check (price_amount > 0 and price_amount <= 100000000),
    currency text not null default 'TZS' check (currency = 'TZS'),
    status text not null default 'draft' check (status in ('draft','listed','archived')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint creator_products_owner_slug_key unique (owner_id, slug),
);
create index if not exists idx_creator_products_listed_created
  on public.creator_products(status, created_at desc);
create index if not exists idx_creator_products_owner_created
  on public.creator_products(owner_id, created_at desc);
alter table public.creator_products enable row level security;
drop policy if exists "owners read own creator products" on public.creator_products;
create policy "owners read own creator products"
  on public.creator_products for select to authenticated
  using ((select auth.uid()) = owner_id);
drop policy if exists "buyers read listed creator products" on public.creator_products;
create policy "buyers read listed creator products"
  on public.creator_products for select to authenticated
  using (status = 'listed');
drop policy if exists "owners create creator products" on public.creator_products;
create policy "owners create creator products"
  on public.creator_products for insert to authenticated
  with check ((select auth.uid()) = owner_id);
drop policy if exists "owners update creator products" on public.creator_products;
create policy "owners update creator products"
  on public.creator_products for update to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);
revoke all on public.creator_products from anon, authenticated;
grant select, insert, update on public.creator_products to authenticated;
grant all on public.creator_products to service_role;
