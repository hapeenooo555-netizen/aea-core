-- Owner-scoped requests to distribute creator products to social destinations.
-- This records intent only; it does not authorize or perform external publishing.
create table if not exists public.creator_distribution_jobs (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    product_id uuid not null references public.creator_products(id) on delete cascade,
    destination text not null check (destination in ('pinterest','facebook','instagram','tiktok','youtube')),
    status text not null check (status in ('pending_connection','blocked_unsupported','pending_approval','queued','publishing','published','failed','cancelled')),
    block_reason text,
    provider_post_id text,
    provider_post_url text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint creator_distribution_jobs_owner_product_destination_key
      unique (owner_id, product_id, destination)
);
create index if not exists idx_creator_distribution_jobs_owner_created
  on public.creator_distribution_jobs(owner_id, created_at desc);
create index if not exists idx_creator_distribution_jobs_status_created
  on public.creator_distribution_jobs(status, created_at);
alter table public.creator_distribution_jobs enable row level security;
drop policy if exists "owners read own creator distribution jobs" on public.creator_distribution_jobs;
create policy "owners read own creator distribution jobs"
  on public.creator_distribution_jobs for select to authenticated
  using ((select auth.uid()) = owner_id);
drop policy if exists "owners create own creator distribution jobs" on public.creator_distribution_jobs;
create policy "owners create own creator distribution jobs"
  on public.creator_distribution_jobs for insert to authenticated
  with check (
    (select auth.uid()) = owner_id
    and exists (
      select 1 from public.creator_products p
      where p.id = product_id and p.owner_id = (select auth.uid())
    )
  );
revoke all on public.creator_distribution_jobs from anon, authenticated;
grant select, insert on public.creator_distribution_jobs to authenticated;
grant all on public.creator_distribution_jobs to service_role;
