-- Record the production hardening applied after the Phase 1 migrations.
-- Keep trigger functions on a fixed search path.
alter function public.set_freelancer_projects_updated_at() set search_path = public;
alter function public.set_freelancer_foundation_updated_at() set search_path = public;

-- Restrict model-registry row access explicitly to signed-in owners.
drop policy if exists aea_ai_models_owner_all on public.aea_ai_models;
create policy aea_ai_models_owner_all on public.aea_ai_models
  for all to authenticated
  using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);
grant select, insert, update, delete on public.aea_ai_models to authenticated;
grant all on public.aea_ai_models to service_role;
