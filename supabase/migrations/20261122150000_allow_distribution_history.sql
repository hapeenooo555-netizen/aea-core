-- Permit a product/platform to have multiple historical distribution attempts.
-- Only simultaneous draft duplicates are prevented by the partial unique index.
DROP INDEX IF EXISTS public.creator_distribution_jobs_owner_product_destination_uidx;
