-- Ensure the encrypted Meta credential vault tracks access-token expiry.
ALTER TABLE public.meta_oauth_credentials
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
