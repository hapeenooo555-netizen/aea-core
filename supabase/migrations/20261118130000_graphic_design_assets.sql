-- Private owner-scoped Graphic Design generation jobs and rendered assets.
-- Provider credentials remain in deployment secrets; no public asset URLs are stored.
create table if not exists public.graphic_design_jobs (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  provider text not null check (char_length(provider) between 1 and 80),
  provider_task_id text,
  model text not null check (char_length(model) between 1 and 160),
  prompt text not null check (char_length(prompt) between 1 and 12000),
  aspect_ratio text not null default '1:1'
    check (aspect_ratio in ('1:1','4:5','9:16','16:9','3:2')),
  status text not null default 'pending'
    check (status in ('pending','in_progress','completed','failed')),
  asset_path text,
  mime_type text check (mime_type in ('image/png','image/jpeg','image/webp')),
  width integer check (width is null or width > 0),
  height integer check (height is null or height > 0),
  error text,
  provider_metadata jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  check (
    (status = 'completed' and asset_path is not null and mime_type is not null)
    or status <> 'completed'
  )
);

create index if not exists graphic_design_jobs_owner_created_idx
  on public.graphic_design_jobs(owner_id, created_at desc);
create unique index if not exists graphic_design_jobs_provider_task_idx
  on public.graphic_design_jobs(provider, provider_task_id)
  where provider_task_id is not null;

alter table public.graphic_design_jobs enable row level security;
drop policy if exists "owners read own graphic design jobs" on public.graphic_design_jobs;
create policy "owners read own graphic design jobs"
  on public.graphic_design_jobs for select to authenticated
  using ((select auth.uid()) = owner_id);
drop policy if exists "owners create own graphic design jobs" on public.graphic_design_jobs;
create policy "owners create own graphic design jobs"
  on public.graphic_design_jobs for insert to authenticated
  with check ((select auth.uid()) = owner_id);
drop policy if exists "owners update own graphic design jobs" on public.graphic_design_jobs;
create policy "owners update own graphic design jobs"
  on public.graphic_design_jobs for update to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);

revoke all on public.graphic_design_jobs from anon, authenticated;
grant select, insert, update on public.graphic_design_jobs to authenticated;
grant all on public.graphic_design_jobs to service_role;

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'aea-design-assets',
  'aea-design-assets',
  false,
  20971520,
  array['image/png','image/jpeg','image/webp']
)
on conflict (id) do update set
  public = false,
  file_size_limit = 20971520,
  allowed_mime_types = array['image/png','image/jpeg','image/webp'];

drop policy if exists "owners read own AEA design assets" on storage.objects;
create policy "owners read own AEA design assets"
  on storage.objects for select to authenticated
  using (
    bucket_id = 'aea-design-assets'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

drop policy if exists "owners upload own AEA design assets" on storage.objects;
create policy "owners upload own AEA design assets"
  on storage.objects for insert to authenticated
  with check (
    bucket_id = 'aea-design-assets'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

drop policy if exists "owners update own AEA design assets" on storage.objects;
create policy "owners update own AEA design assets"
  on storage.objects for update to authenticated
  using (
    bucket_id = 'aea-design-assets'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  )
  with check (
    bucket_id = 'aea-design-assets'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

drop policy if exists "owners delete own AEA design assets" on storage.objects;
create policy "owners delete own AEA design assets"
  on storage.objects for delete to authenticated
  using (
    bucket_id = 'aea-design-assets'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );
