-- Owner-scoped, versioned Prompt Intelligence registry.
create table if not exists public.prompt_intelligence_registry (
  id uuid primary key default gen_random_uuid(),
  owner_id uuid not null references auth.users(id) on delete cascade,
  prompt_key text not null check (length(prompt_key) between 3 and 120),
  display_name text not null check (length(display_name) between 1 and 200),
  domain text not null check (domain in ('video','graphic_design','marketing','copywriting','business_documents','general')),
  active_version integer not null default 1 check (active_version >= 1),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique(owner_id, prompt_key),
  unique(id, owner_id)
);

create table if not exists public.prompt_intelligence_versions (
  id uuid primary key default gen_random_uuid(),
  registry_id uuid not null,
  owner_id uuid not null references auth.users(id) on delete cascade,
  version integer not null check (version >= 1),
  system_prompt text not null check (length(system_prompt) between 10 and 20000),
  configuration jsonb not null default '{}'::jsonb,
  change_note text not null default '',
  content_sha256 text not null check (content_sha256 ~ '^[a-f0-9]{64}$'),
  created_at timestamptz not null default now(),
  foreign key (registry_id, owner_id) references public.prompt_intelligence_registry(id, owner_id) on delete cascade,
  unique(registry_id, version)
);

create index if not exists prompt_intelligence_registry_owner_updated_idx
  on public.prompt_intelligence_registry(owner_id, updated_at desc);
create index if not exists prompt_intelligence_versions_owner_registry_idx
  on public.prompt_intelligence_versions(owner_id, registry_id, version desc);

alter table public.prompt_intelligence_registry enable row level security;
alter table public.prompt_intelligence_versions enable row level security;

drop policy if exists "owners manage own prompt registry" on public.prompt_intelligence_registry;
create policy "owners manage own prompt registry" on public.prompt_intelligence_registry
  for all to authenticated using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);

drop policy if exists "owners read own prompt versions" on public.prompt_intelligence_versions;
create policy "owners read own prompt versions" on public.prompt_intelligence_versions
  for select to authenticated using ((select auth.uid()) = owner_id);

drop policy if exists "owners create own prompt versions" on public.prompt_intelligence_versions;
create policy "owners create own prompt versions" on public.prompt_intelligence_versions
  for insert to authenticated with check ((select auth.uid()) = owner_id);

comment on table public.prompt_intelligence_versions is
  'Append-only prompt versions. Existing version content is never updated; rollback changes only the registry active_version pointer.';
