-- Preserve source narration and record the language currently shown to the creator.
alter table public.video_project_scenes
  add column if not exists original_narration text,
  add column if not exists script_language text not null default 'en';

alter table public.video_project_scenes
  drop constraint if exists video_project_scenes_script_language_check;

alter table public.video_project_scenes
  add constraint video_project_scenes_script_language_check
  check (script_language in ('sw', 'en'));
