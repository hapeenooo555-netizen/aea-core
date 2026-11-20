-- Enforce one distribution request per creator product and destination.
-- The creator_distribution_jobs table already exists in the deployed community foundation.
-- Duplicate preflight was checked against the live database before adding this constraint.
create unique index if not exists creator_distribution_jobs_owner_product_destination_uidx
  on public.creator_distribution_jobs(owner_id, product_id, destination);
