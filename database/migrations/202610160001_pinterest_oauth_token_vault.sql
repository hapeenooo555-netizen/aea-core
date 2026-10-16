-- Server-only Pinterest OAuth token vault.
-- The backend encrypts all credential values before they reach this table.
CREATE TABLE IF NOT EXISTS public.pinterest_oauth_tokens (
    owner_id UUID PRIMARY KEY REFERENCES auth.users(id) ON DELETE CASCADE,
    encrypted_access_token TEXT NOT NULL,
    encrypted_refresh_token TEXT,
    token_type TEXT NOT NULL DEFAULT 'bearer',
    expires_at TIMESTAMPTZ,
    scopes TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
    external_account_id TEXT,
    display_name TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE public.pinterest_oauth_tokens ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON TABLE public.pinterest_oauth_tokens FROM PUBLIC, anon, authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.pinterest_oauth_tokens TO service_role;
COMMENT ON TABLE public.pinterest_oauth_tokens IS
'Server-only Pinterest OAuth token vault. Credentials must be encrypted by the backend using PINTEREST_TOKEN_ENCRYPTION_KEY before storage.';
