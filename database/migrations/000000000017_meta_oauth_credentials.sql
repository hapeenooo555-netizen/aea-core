-- Meta OAuth credential vault. Review before applying; never run automatically.
BEGIN;
CREATE TABLE IF NOT EXISTS public.meta_oauth_credentials (
    owner_id TEXT PRIMARY KEY,
    encrypted_credentials TEXT NOT NULL,
    meta_user_id TEXT NOT NULL,
    display_name TEXT,
    scopes TEXT[] NOT NULL DEFAULT '{}',
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.meta_oauth_credentials ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.meta_oauth_credentials FROM anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.meta_oauth_credentials TO service_role;
COMMENT ON TABLE public.meta_oauth_credentials IS
'Encrypted Meta OAuth credentials. Only the server-side service role can access this table. Encryption key must be stored separately as a deployment secret.';
COMMIT;
