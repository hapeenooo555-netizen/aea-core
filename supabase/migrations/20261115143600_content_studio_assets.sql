-- Durable, owner-scoped Content Studio drafts with immutable version history.
-- No browser-supplied owner IDs are trusted; all writes use the authenticated JWT.

create table if not exists public.content_asset_versions (
    id uuid primary key default gen_random_uuid(),
    asset_id uuid not null,
    owner_id uuid not null references auth.users(id) on delete cascade,
    version integer not null check (version >= 1),
    idempotency_key text not null,
    title text not null,
    content_type text not null,
    niche text not null,
    prompt text not null,
    content jsonb not null,
    status text not null default 'draft'
        check (status in ('draft', 'in_review', 'approved', 'published', 'archived')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint content_asset_versions_asset_version_key unique (asset_id, version),
    constraint content_asset_versions_owner_idempotency_key unique (owner_id, idempotency_key)
);

create index if not exists idx_content_asset_versions_owner_updated
    on public.content_asset_versions (owner_id, updated_at desc);
create index if not exists idx_content_asset_versions_asset_version
    on public.content_asset_versions (asset_id, version desc);

alter table public.content_asset_versions enable row level security;

drop policy if exists "owners can read content asset versions" on public.content_asset_versions;
create policy "owners can read content asset versions"
    on public.content_asset_versions
    for select
    to authenticated
    using ((select auth.uid()) = owner_id);

drop policy if exists "owners can create content asset versions" on public.content_asset_versions;
create policy "owners can create content asset versions"
    on public.content_asset_versions
    for insert
    to authenticated
    with check ((select auth.uid()) = owner_id);

-- The table is append-only through the Data API. No UPDATE/DELETE is granted.
revoke all on public.content_asset_versions from anon, authenticated;
grant select, insert on public.content_asset_versions to authenticated;
grant all on public.content_asset_versions to service_role;

-- The view exposes only the newest version of each logical asset and evaluates
-- permissions/RLS as the querying role (Postgres 15+).
create or replace view public.content_assets_latest
with (security_invoker = true)
as
select distinct on (owner_id, asset_id)
    id, asset_id, owner_id, version, idempotency_key, title, content_type,
    niche, prompt, content, status, created_at, updated_at
from public.content_asset_versions
order by owner_id, asset_id, version desc;

revoke all on public.content_assets_latest from anon, authenticated;
grant select on public.content_assets_latest to authenticated;
