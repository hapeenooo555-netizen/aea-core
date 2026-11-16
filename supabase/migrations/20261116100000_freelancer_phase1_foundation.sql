-- Phase 1 Freelancer foundation: owner-scoped clients and service catalogue.
create table if not exists public.freelancer_clients (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    name text not null check (char_length(trim(name)) between 1 and 200),
    email text,
    phone text,
    company text,
    timezone text not null default 'Africa/Dar_es_Salaam',
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (owner_id, id)
);
create index if not exists freelancer_clients_owner_updated_idx
    on public.freelancer_clients(owner_id, updated_at desc);
alter table public.freelancer_clients enable row level security;
drop policy if exists freelancer_clients_select_own on public.freelancer_clients;
create policy freelancer_clients_select_own on public.freelancer_clients
    for select to authenticated using (owner_id = (select auth.uid()));
drop policy if exists freelancer_clients_insert_own on public.freelancer_clients;
create policy freelancer_clients_insert_own on public.freelancer_clients
    for insert to authenticated with check (owner_id = (select auth.uid()));
drop policy if exists freelancer_clients_update_own on public.freelancer_clients;
create policy freelancer_clients_update_own on public.freelancer_clients
    for update to authenticated using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));
drop policy if exists freelancer_clients_delete_own on public.freelancer_clients;
create policy freelancer_clients_delete_own on public.freelancer_clients
    for delete to authenticated using (owner_id = (select auth.uid()));
grant select, insert, update, delete on public.freelancer_clients to authenticated;

create table if not exists public.freelancer_services (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    name text not null check (char_length(trim(name)) between 1 and 160),
    category text not null,
    description text,
    pricing_model text not null default 'custom'
        check (pricing_model in ('fixed', 'hourly', 'custom')),
    starting_price numeric(12,2) check (starting_price is null or starting_price >= 0),
    currency text not null default 'TZS' check (currency ~ '^[A-Z]{3}$'),
    is_active boolean not null default true,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (owner_id, id)
);
create index if not exists freelancer_services_owner_active_idx
    on public.freelancer_services(owner_id, is_active, updated_at desc);
alter table public.freelancer_services enable row level security;
drop policy if exists freelancer_services_select_own on public.freelancer_services;
create policy freelancer_services_select_own on public.freelancer_services
    for select to authenticated using (owner_id = (select auth.uid()));
drop policy if exists freelancer_services_insert_own on public.freelancer_services;
create policy freelancer_services_insert_own on public.freelancer_services
    for insert to authenticated with check (owner_id = (select auth.uid()));
drop policy if exists freelancer_services_update_own on public.freelancer_services;
create policy freelancer_services_update_own on public.freelancer_services
    for update to authenticated using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));
drop policy if exists freelancer_services_delete_own on public.freelancer_services;
create policy freelancer_services_delete_own on public.freelancer_services
    for delete to authenticated using (owner_id = (select auth.uid()));
grant select, insert, update, delete on public.freelancer_services to authenticated;

alter table public.freelancer_projects
    add column if not exists client_id uuid,
    add column if not exists scope text,
    add column if not exists due_date date;
do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname = 'freelancer_projects_owner_client_fk'
    ) then
        alter table public.freelancer_projects
            add constraint freelancer_projects_owner_client_fk
            foreign key (owner_id, client_id)
            references public.freelancer_clients(owner_id, id)
            on delete set null (client_id);
    end if;
end $$;
create index if not exists freelancer_projects_owner_status_idx
    on public.freelancer_projects(owner_id, status, updated_at desc);

create or replace function public.set_freelancer_foundation_updated_at()
returns trigger language plpgsql as $$
begin
    new.updated_at = now();
    return new;
end;
$$;
drop trigger if exists freelancer_clients_set_updated_at on public.freelancer_clients;
create trigger freelancer_clients_set_updated_at before update on public.freelancer_clients
for each row execute function public.set_freelancer_foundation_updated_at();
drop trigger if exists freelancer_services_set_updated_at on public.freelancer_services;
create trigger freelancer_services_set_updated_at before update on public.freelancer_services
for each row execute function public.set_freelancer_foundation_updated_at();
