-- Digistore24 per-user API credential storage and short-lived connect state.
-- The API key ciphertext is only accessible to the backend service role.
create table if not exists public.digistore24_user_connections (
  user_id uuid primary key references auth.users(id) on delete cascade,
  api_key_ciphertext text not null,
  connected_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table public.digistore24_user_connections enable row level security;
revoke all on public.digistore24_user_connections from anon, authenticated;
grant all on public.digistore24_user_connections to service_role;

create table if not exists public.digistore24_connect_states (
  state_hash text primary key,
  user_id uuid not null references auth.users(id) on delete cascade,
  request_token text not null,
  expires_at timestamptz not null,
  consumed_at timestamptz,
  created_at timestamptz not null default now()
);

alter table public.digistore24_connect_states enable row level security;
revoke all on public.digistore24_connect_states from anon, authenticated;
grant all on public.digistore24_connect_states to service_role;

create index if not exists digistore24_connect_states_expiry_idx
  on public.digistore24_connect_states (expires_at);
