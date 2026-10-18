ALTER TABLE public.missions ADD COLUMN IF NOT EXISTS error text;
NOTIFY pgrst, 'reload schema';
