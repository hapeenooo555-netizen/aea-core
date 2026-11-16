-- Owner-scoped AI model registry. Credentials remain in deployment secrets, never in this table.
create table if not exists public.aea_ai_models (
 id uuid primary key default gen_random_uuid(), owner_id uuid not null references auth.users(id) on delete cascade,
 provider text not null check(length(provider) between 1 and 80), model_key text not null check(length(model_key) between 1 and 160),
 display_name text not null check(length(display_name) between 1 and 160), capabilities text[] not null default '{}',
 enabled boolean not null default true, priority integer not null default 100, cost_per_unit numeric(14,6),
 config jsonb not null default '{}'::jsonb, created_at timestamptz not null default now(), updated_at timestamptz not null default now(),
 unique(owner_id,provider,model_key)
);
create index if not exists aea_ai_models_owner_enabled_priority_idx on public.aea_ai_models(owner_id,enabled,priority);
alter table public.aea_ai_models enable row level security;
drop policy if exists aea_ai_models_owner_all on public.aea_ai_models;
create policy aea_ai_models_owner_all on public.aea_ai_models for all using(auth.uid()=owner_id) with check(auth.uid()=owner_id);
