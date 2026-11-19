-- Persist bounded Runway rejection diagnostics on each video project scene.
alter table public.video_project_scenes
  add column if not exists provider_error jsonb,
  add column if not exists http_status integer;
