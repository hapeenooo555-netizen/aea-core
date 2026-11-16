-- Durable queue for long-running final MP4 assembly.
create table if not exists public.video_assembly_jobs (
  id uuid primary key,
  project_id uuid not null references public.video_projects(id) on delete cascade,
  owner_id uuid not null references auth.users(id) on delete cascade,
  status text not null default 'queued'
    check (status in ('queued','running','completed','failed')),
  attempts integer not null default 0 check (attempts >= 0),
  max_attempts integer not null default 3 check (max_attempts between 1 and 10),
  available_at timestamptz not null default now(),
  locked_at timestamptz,
  locked_by text,
  last_error text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_video_assembly_jobs_claim
  on public.video_assembly_jobs(status, available_at, created_at);
create unique index if not exists idx_video_assembly_one_active_job_per_project
  on public.video_assembly_jobs(project_id)
  where status in ('queued','running');
alter table public.video_assembly_jobs enable row level security;
drop policy if exists "owners read own video assembly jobs" on public.video_assembly_jobs;
create policy "owners read own video assembly jobs"
  on public.video_assembly_jobs for select to authenticated
  using ((select auth.uid()) = owner_id);
revoke all on public.video_assembly_jobs from anon, authenticated;
grant select on public.video_assembly_jobs to authenticated;
grant all on public.video_assembly_jobs to service_role;

-- Claim a single job atomically. Expired worker leases are eligible for recovery.
create or replace function public.claim_video_assembly_job(p_worker_id text)
returns setof public.video_assembly_jobs
language plpgsql
security definer
set search_path = public
as $$
begin
  return query
  with candidate as (
    select id
    from public.video_assembly_jobs
    where attempts < max_attempts
      and available_at <= now()
      and (
        status = 'queued'
        or (status = 'running' and locked_at < now() - interval '15 minutes')
      )
    order by created_at
    for update skip locked
    limit 1
  )
  update public.video_assembly_jobs j
  set status = 'running',
      attempts = j.attempts + 1,
      locked_at = now(),
      locked_by = p_worker_id,
      updated_at = now()
  from candidate
  where j.id = candidate.id
  returning j.*;
end;
$$;

create or replace function public.finish_video_assembly_job(
  p_job_id uuid,
  p_worker_id text,
  p_success boolean,
  p_error text default null
)
returns boolean
language plpgsql
security definer
set search_path = public
as $$
declare affected integer;
begin
  update public.video_assembly_jobs
  set status = case when p_success then 'completed'
                    when attempts >= max_attempts then 'failed'
                    else 'queued' end,
      available_at = case when p_success or attempts >= max_attempts
                          then available_at
                          else now() + make_interval(secs => least(300, attempts * 30)) end,
      locked_at = null,
      locked_by = null,
      last_error = case when p_success then null else left(coalesce(p_error, 'Assembly failed'), 1000) end,
      updated_at = now()
  where id = p_job_id and status = 'running' and locked_by = p_worker_id;
  get diagnostics affected = row_count;
  return affected = 1;
end;
$$;

revoke all on function public.claim_video_assembly_job(text) from public, anon, authenticated;
revoke all on function public.finish_video_assembly_job(uuid,text,boolean,text) from public, anon, authenticated;
grant execute on function public.claim_video_assembly_job(text) to service_role;
grant execute on function public.finish_video_assembly_job(uuid,text,boolean,text) to service_role;
