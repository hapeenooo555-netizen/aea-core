-- Durable owner-scoped Runway video generation jobs.
create table if not exists public.video_generation_jobs (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete cascade,
    provider text not null check (provider = 'runway'),
    provider_task_id text not null unique,
    model text not null,
    prompt text not null check (char_length(prompt) between 1 and 1000),
    duration_seconds integer not null check (duration_seconds between 2 and 10),
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

-- Long-form projects are assembled from multiple short provider-generated scenes.
create table if not exists public.video_projects (
    id uuid primary key,
    owner_id uuid not null references auth.users(id) on delete cascade,
    title text not null check (char_length(title) between 1 and 180),
    format text not null check (format in ('short','long_form','storytelling','documentary','advertisement')),
    target_duration_seconds integer not null check (target_duration_seconds between 15 and 7200),
    aspect_ratio text not null check (aspect_ratio in ('9:16','16:9')),
    visual_style text not null default 'cinematic_realistic',
    presenter_mode text not null default 'faceless' check (presenter_mode in ('faceless','realistic_human','mixed')),
    status text not null default 'draft' check (status in ('draft','ready','generating','assembling','completed','failed')),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now()
);
create table if not exists public.video_project_scenes (
    id uuid primary key,
    project_id uuid not null references public.video_projects(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    scene_number integer not null check (scene_number > 0),
    title text not null,
    narration text not null default '',
    visual_prompt text not null check (char_length(visual_prompt) between 1 and 1000),
    duration_seconds integer not null check (duration_seconds between 2 and 10),
    status text not null default 'pending' check (status in ('pending','generating','completed','failed')),
    generation_job_id uuid references public.video_generation_jobs(id) on delete set null,
    video_url text,
    created_at timestamptz not null default now(),
    unique(project_id, scene_number)
);
create index if not exists idx_video_projects_owner_created on public.video_projects(owner_id, created_at desc);
create index if not exists idx_video_project_scenes_project_order on public.video_project_scenes(project_id, scene_number);
alter table public.video_projects enable row level security;
alter table public.video_project_scenes enable row level security;
create policy "owners manage own video projects" on public.video_projects
  for all to authenticated using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);
create policy "owners manage own video project scenes" on public.video_project_scenes
  for all to authenticated using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);
revoke all on public.video_projects, public.video_project_scenes from anon, authenticated;
grant select, insert, update, delete on public.video_projects, public.video_project_scenes to authenticated;
grant all on public.video_projects, public.video_project_scenes to service_role;

-- Store a reusable character reference bible for consistent storytelling scenes.
alter table public.video_projects
  add column if not exists character_bible jsonb not null default '{}'::jsonb;
