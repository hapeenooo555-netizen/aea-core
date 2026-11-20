-- Remove Digistore24-owned storage and historical marketplace-only mission data.
-- Keep the earlier Pinterest onboarding mission, its Pinterest approval, checkpoint,
-- Pinterest status step, and orchestration history. Only scrub provider wording.
begin;

-- This completed draft was created solely from the retired provider's marketplace.
-- Its step rows use NO ACTION foreign keys, so remove them before deleting the mission.
delete from public.mission_steps
where mission_id in (
  select id from public.missions
  where title ilike '%Digistore24%'
    and description ilike '%marketplace product%'
);

delete from public.missions
where title ilike '%Digistore24%'
  and description ilike '%marketplace product%';

-- Preserve the Pinterest onboarding mission and its approval/checkpoint relationships.
update public.missions
set title = 'Start affiliate marketing on Pinterest and check my Pinterest connection first.',
    description = 'Start affiliate marketing on Pinterest. Check my Pinterest connection first. If Pinterest is not connected, start the onboarding process and stop for my approval before any authorization step.',
    objective = 'Start affiliate marketing on Pinterest. Check my Pinterest connection first. If Pinterest is not connected, start the onboarding process and stop for my approval before any authorization step.',
    updated_at = now()
where title ilike '%Digistore24%'
  and description ilike '%Pinterest connection%';

drop table if exists public.digistore24_connect_states;
drop table if exists public.digistore24_user_connections;

commit;
