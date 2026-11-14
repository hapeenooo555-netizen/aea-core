-- Per-user, per-flow Meta OAuth credential vault.
-- Tokens are Fernet-encrypted in AEA Core before persistence.
create table if not exists public.meta_user_connections (
  owner_id uuid not null references auth.users(id) on delete cascade,
  purpose text not null check (purpose in ('facebook_login', 'whatsapp_embedded_signup')),
  encrypted_access_token text not null,
  token_expires_at timestamptz,
  granted_scopes text[] not null default array[]::text[],
  assets jsonb not null default '{"facebook_pages":[],"instagram_accounts":[],"whatsapp_accounts":[],"business_portfolios":[]}'::jsonb,
  updated_at timestamptz not null default now(),
  primary key (owner_id, purpose)
);

-- Upgrade safely if an earlier single-token table definition already exists.
alter table public.meta_user_connections add column if not exists purpose text not null default 'facebook_login';
alter table public.meta_user_connections drop constraint if exists meta_user_connections_pkey;
alter table public.meta_user_connections add constraint meta_user_connections_pkey primary key (owner_id, purpose);

alter table public.meta_user_connections enable row level security;
revoke all on table public.meta_user_connections from public, anon, authenticated;
grant select, insert, update, delete on table public.meta_user_connections to service_role;
create index if not exists meta_user_connections_purpose_idx on public.meta_user_connections (purpose);

create table if not exists public.meta_oauth_states (
  state_hash text primary key,
  owner_id uuid not null references auth.users(id) on delete cascade,
  purpose text not null check (purpose in ('facebook_login', 'whatsapp_embedded_signup')),
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);
alter table public.meta_oauth_states enable row level security;
revoke all on table public.meta_oauth_states from public, anon, authenticated;
grant select, insert, update, delete on table public.meta_oauth_states to service_role;
create index if not exists meta_oauth_states_expiry_idx on public.meta_oauth_states (expires_at);
create index if not exists meta_oauth_states_owner_idx on public.meta_oauth_states (owner_id);

comment on table public.meta_user_connections is
'Server-only per-owner, per-flow Meta credential vault. Tokens must be Fernet-encrypted using META_CREDENTIAL_ENCRYPTION_KEY.';
comment on table public.meta_oauth_states is
'Server-only one-time Meta OAuth state. Only SHA-256 state hashes are stored.';
