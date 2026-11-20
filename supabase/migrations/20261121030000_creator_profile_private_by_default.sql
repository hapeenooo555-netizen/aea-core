-- Creator profiles must be private until the creator explicitly opts in.
-- Existing rows are preserved; this migration changes only the default for new profiles.
alter table public.creator_profiles
  alter column is_public set default false;
