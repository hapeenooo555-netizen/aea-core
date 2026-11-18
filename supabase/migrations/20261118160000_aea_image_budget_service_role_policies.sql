-- Make the service-role-only intent explicit to Supabase security advisors.
-- No anon/authenticated policies are created; browser access remains denied.
create policy "service role manages AEA image budget"
  on public.aea_image_budget
  for all to service_role
  using ((select auth.role()) = 'service_role')
  with check ((select auth.role()) = 'service_role');

create policy "service role manages AEA image budget reservations"
  on public.aea_image_budget_reservations
  for all to service_role
  using ((select auth.role()) = 'service_role')
  with check ((select auth.role()) = 'service_role');
