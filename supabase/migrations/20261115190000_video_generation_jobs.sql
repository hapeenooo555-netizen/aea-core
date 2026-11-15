-- Durable owner-scoped Runway video generation jobs.
create table if not exists public.video_generation_jobs (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete cascade,
    provider text not null check (provider = 'runway'),
    provider_task_id text not null unique,
    model text not null,
    prompt text not null check (char_length(prompt) between 10 and 1800),
    duration_seconds integer not null check (duration_seconds between 5 and 10),
    aspect_ratio text not null check (aspect_ratio in ('9:16','16:9')),
    status text not null default 'pending' check (status in ('pending','in_progress','completed','failed','cancelled')),
    video_url text,
    error text,
    provider_response jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create index if not exists idx_video_generation_jobs_owner_created
  on public.video_generation_jobs(owner_id, created_at desc);
alter table public.video_generation_jobs enable row level security;
drop policy if exists "owners read own video generation jobs" on public.video_generation_jobs;
create policy "owners read own video generation jobs"
  on public.video_generation_jobs for select to authenticated
  using ((select auth.uid()) = owner_id);
drop policy if exists "owners create own video generation jobs" on public.video_generation_jobs;
create policy "owners create own video generation jobs"
  on public.video_generation_jobs for insert to authenticated
  with check ((select auth.uid()) = owner_id);
drop policy if exists "owners update own video generation jobs" on public.video_generation_jobs;
create policy "owners update own video generation jobs"
  on public.video_generation_jobs for update to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);
revoke all on public.video_generation_jobs from anon, authenticated;
grant select, insert, update on public.video_generation_jobs to authenticated;
grant all on public.video_generation_jobs to service_role;
