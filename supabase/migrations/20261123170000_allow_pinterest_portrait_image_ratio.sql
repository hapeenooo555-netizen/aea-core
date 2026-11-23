-- Pinterest Pin artwork uses a portrait 2:3 canvas. The original
-- graphic_design_jobs constraint omitted 2:3 even though the API and provider
-- explicitly support it, causing the authenticated job INSERT to fail with 503.
alter table public.graphic_design_jobs
  drop constraint if exists graphic_design_jobs_aspect_ratio_check;

alter table public.graphic_design_jobs
  add constraint graphic_design_jobs_aspect_ratio_check
  check (aspect_ratio = any (array['1:1','4:5','9:16','16:9','3:2','2:3']::text[]));
