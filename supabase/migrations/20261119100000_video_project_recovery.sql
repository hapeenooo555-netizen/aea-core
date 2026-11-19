-- Recoverable Video Studio metadata.
-- Storyboard scenes remain in video_project_scenes; these fields retain the
-- original prompt and the user's scene selection across browser/device changes.
alter table public.video_projects
  add column if not exists prompt text not null default '',
  add column if not exists selected_scene_ids jsonb not null default '[]'::jsonb;

alter table public.video_projects
  drop constraint if exists video_projects_selected_scene_ids_is_array;
alter table public.video_projects
  add constraint video_projects_selected_scene_ids_is_array
  check (jsonb_typeof(selected_scene_ids) = 'array');
