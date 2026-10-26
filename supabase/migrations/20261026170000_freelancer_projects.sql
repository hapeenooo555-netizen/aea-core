-- Persistent project tracking for AEA Freelancer Employee.
-- Apply through the project's normal Supabase migration process before enabling
-- the API in production.
create table if not exists public.freelancer_projects (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    title text not null check (char_length(trim(title)) between 1 and 200),
    job_description text not null check (char_length(trim(job_description)) between 1 and 30000),
    source_url text,
    status text not null default 'lead'
        check (status in ('lead', 'proposal_draft', 'proposal_sent', 'in_progress', 'submitted', 'revision', 'completed', 'cancelled')),
    proposal_text text,
    agreed_amount numeric(12,2) check (agreed_amount is null or agreed_amount >= 0),
    currency text not null default 'USD' check (currency ~ '^[A-Z]{3}$'),
    amount_received numeric(12,2) not null default 0 check (amount_received >= 0),
    notes text,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint freelancer_received_not_over_agreed
      check (agreed_amount is null or amount_received <= agreed_amount)
);

create index if not exists freelancer_projects_owner_updated_idx
    on public.freelancer_projects (owner_id, updated_at desc);

alter table public.freelancer_projects enable row level security;

drop policy if exists freelancer_projects_select_own on public.freelancer_projects;
create policy freelancer_projects_select_own on public.freelancer_projects
    for select to authenticated using (owner_id = (select auth.uid()));

drop policy if exists freelancer_projects_insert_own on public.freelancer_projects;
create policy freelancer_projects_insert_own on public.freelancer_projects
    for insert to authenticated with check (owner_id = (select auth.uid()));

drop policy if exists freelancer_projects_update_own on public.freelancer_projects;
create policy freelancer_projects_update_own on public.freelancer_projects
    for update to authenticated using (owner_id = (select auth.uid()))
    with check (owner_id = (select auth.uid()));

drop policy if exists freelancer_projects_delete_own on public.freelancer_projects;
create policy freelancer_projects_delete_own on public.freelancer_projects
    for delete to authenticated using (owner_id = (select auth.uid()));

grant select, insert, update, delete on public.freelancer_projects to authenticated;

-- Keep updated_at accurate when project fields change.
create or replace function public.set_freelancer_projects_updated_at()
returns trigger
language plpgsql
as $$
begin
    new.updated_at = now();
    return new;
end;
$$;

drop trigger if exists freelancer_projects_set_updated_at on public.freelancer_projects;
create trigger freelancer_projects_set_updated_at
before update on public.freelancer_projects
for each row execute function public.set_freelancer_projects_updated_at();
