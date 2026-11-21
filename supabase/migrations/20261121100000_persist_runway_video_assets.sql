-- Keep provider-generated clips in AEA-owned private storage.
-- video_url remains nullable for legacy records and recovery diagnostics.
alter table public.video_generation_jobs
  add column if not exists video_storage_path text;

alter table public.video_project_scenes
  add column if not exists video_storage_path text;

create index if not exists idx_video_project_scenes_generation_job_owner
  on public.video_project_scenes(generation_job_id, owner_id)
  where generation_job_id is not null;

-- The existing bucket is private and already has owner-scoped Storage RLS policies.
-- Keep its current limits and access model unchanged.
