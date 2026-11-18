-- Atomic, shared trial-budget ledger. The singleton cap defaults to USD 10.
create table if not exists public.aea_image_budget (
  id boolean primary key default true check (id),
  budget_limit_usd numeric(10,2) not null default 10.00 check (budget_limit_usd > 0),
  spent_usd numeric(10,4) not null default 0 check (spent_usd >= 0),
  reserved_usd numeric(10,4) not null default 0 check (reserved_usd >= 0),
  updated_at timestamptz not null default now()
);
insert into public.aea_image_budget (id) values (true) on conflict (id) do nothing;
create table if not exists public.aea_image_budget_reservations (
  job_id uuid primary key,
  amount_usd numeric(10,4) not null check (amount_usd > 0),
  actual_usd numeric(10,4),
  status text not null check (status in ('reserved','settled','released')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
alter table public.aea_image_budget enable row level security;
alter table public.aea_image_budget_reservations enable row level security;
revoke all on public.aea_image_budget from anon, authenticated;
revoke all on public.aea_image_budget_reservations from anon, authenticated;
grant all on public.aea_image_budget to service_role;
grant all on public.aea_image_budget_reservations to service_role;

create or replace function public.aea_reserve_image_budget(
  p_job_id uuid, p_amount_usd numeric, p_budget_limit_usd numeric default 10.00
) returns boolean language plpgsql security definer set search_path = public as $$
declare b public.aea_image_budget%rowtype;
begin
  if p_amount_usd <= 0 or p_budget_limit_usd <= 0 then return false; end if;
  insert into public.aea_image_budget (id, budget_limit_usd)
  values (true, least(p_budget_limit_usd, 10.00))
  on conflict (id) do nothing;
  select * into b from public.aea_image_budget where id = true for update;
  if b.budget_limit_usd > 10.00 then
    update public.aea_image_budget set budget_limit_usd = 10.00 where id = true;
    b.budget_limit_usd := 10.00;
  end if;
  if exists (select 1 from public.aea_image_budget_reservations where job_id = p_job_id) then
    return false;
  end if;
  if b.spent_usd + b.reserved_usd + p_amount_usd > least(b.budget_limit_usd, p_budget_limit_usd) then
    return false;
  end if;
  insert into public.aea_image_budget_reservations(job_id, amount_usd, status)
  values (p_job_id, p_amount_usd, 'reserved');
  update public.aea_image_budget
    set reserved_usd = reserved_usd + p_amount_usd, updated_at = now()
    where id = true;
  return true;
end; $$;

create or replace function public.aea_settle_image_budget(
  p_job_id uuid, p_actual_usd numeric
) returns boolean language plpgsql security definer set search_path = public as $$
declare r public.aea_image_budget_reservations%rowtype;
begin
  if p_actual_usd < 0 then return false; end if;
  select * into r from public.aea_image_budget_reservations
    where job_id = p_job_id for update;
  if not found or r.status <> 'reserved' then return false; end if;
  -- Refuse settlement above the preflight reservation; a model's configured
  -- maximum price must never be silently exceeded by the application ledger.
  if p_actual_usd > r.amount_usd then return false; end if;
  update public.aea_image_budget
    set reserved_usd = greatest(0, reserved_usd - r.amount_usd),
        spent_usd = spent_usd + p_actual_usd, updated_at = now()
    where id = true;
  update public.aea_image_budget_reservations
    set actual_usd = p_actual_usd, status = 'settled', updated_at = now()
    where job_id = p_job_id;
  return true;
end; $$;

create or replace function public.aea_release_image_budget(p_job_id uuid)
returns boolean language plpgsql security definer set search_path = public as $$
declare r public.aea_image_budget_reservations%rowtype;
begin
  select * into r from public.aea_image_budget_reservations
    where job_id = p_job_id for update;
  if not found or r.status <> 'reserved' then return false; end if;
  update public.aea_image_budget
    set reserved_usd = greatest(0, reserved_usd - r.amount_usd), updated_at = now()
    where id = true;
  update public.aea_image_budget_reservations
    set status = 'released', updated_at = now() where job_id = p_job_id;
  return true;
end; $$;
revoke all on function public.aea_reserve_image_budget(uuid,numeric,numeric) from public, anon, authenticated;
revoke all on function public.aea_settle_image_budget(uuid,numeric) from public, anon, authenticated;
revoke all on function public.aea_release_image_budget(uuid) from public, anon, authenticated;
grant execute on function public.aea_reserve_image_budget(uuid,numeric,numeric) to service_role;
grant execute on function public.aea_settle_image_budget(uuid,numeric) to service_role;
grant execute on function public.aea_release_image_budget(uuid) to service_role;
