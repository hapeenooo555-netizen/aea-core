-- Creator affiliate payout requests with row-level commission reservation.
-- Only the promoter can request their own payable commissions. Only active
-- AEA Marketplace Admins can approve, reject, or confirm an actual payout.

alter table public.creator_order_items
  drop constraint if exists creator_order_items_commission_status_check;

alter table public.creator_order_items
  add constraint creator_order_items_commission_status_check
  check (commission_status = any (array[
    'not_applicable','rate_unconfigured','pending','approved',
    'payable','payout_requested','void','paid'
  ]::text[]));

create table if not exists public.creator_payout_requests (
  id uuid primary key default gen_random_uuid(),
  promoter_id uuid not null references auth.users(id) on delete restrict,
  amount bigint not null check (amount > 0),
  currency text not null default 'TZS' check (currency = 'TZS'),
  payment_method text not null check (payment_method in ('mobile_money','bank_transfer')),
  status text not null default 'pending'
    check (status in ('pending','approved','rejected','paid')),
  rejection_reason text,
  payout_reference text,
  reviewed_by uuid references auth.users(id) on delete restrict,
  reviewed_at timestamptz,
  paid_at timestamptz,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create table if not exists public.creator_payout_request_items (
  payout_request_id uuid not null references public.creator_payout_requests(id) on delete restrict,
  order_item_id uuid not null references public.creator_order_items(id) on delete restrict,
  commission_amount bigint not null check (commission_amount > 0),
  created_at timestamptz not null default now(),
  primary key (payout_request_id, order_item_id)
);

create index if not exists creator_payout_requests_promoter_created_idx
  on public.creator_payout_requests (promoter_id, created_at desc);
create index if not exists creator_payout_requests_status_created_idx
  on public.creator_payout_requests (status, created_at desc);
create index if not exists creator_payout_items_request_idx
  on public.creator_payout_request_items (payout_request_id);

alter table public.creator_payout_requests enable row level security;
alter table public.creator_payout_request_items enable row level security;

drop policy if exists "promoters and admins read payout requests" on public.creator_payout_requests;
create policy "promoters and admins read payout requests"
  on public.creator_payout_requests for select to authenticated
  using (
    promoter_id = (select auth.uid())
    or exists (
      select 1 from public.aea_marketplace_admins a
      where a.user_id = (select auth.uid()) and a.active = true
    )
  );

drop policy if exists "promoters and admins read payout request items" on public.creator_payout_request_items;
create policy "promoters and admins read payout request items"
  on public.creator_payout_request_items for select to authenticated
  using (
    exists (
      select 1 from public.creator_payout_requests r
      where r.id = payout_request_id
        and (
          r.promoter_id = (select auth.uid())
          or exists (
            select 1 from public.aea_marketplace_admins a
            where a.user_id = (select auth.uid()) and a.active = true
          )
        )
    )
  );

revoke all on public.creator_payout_requests from anon, authenticated;
revoke all on public.creator_payout_request_items from anon, authenticated;
grant select on public.creator_payout_requests to authenticated;
grant select on public.creator_payout_request_items to authenticated;

create or replace function public.create_my_creator_payout_request(p_payment_method text)
returns table (
  id uuid,
  amount bigint,
  currency text,
  payment_method text,
  status text,
  created_at timestamptz
)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_promoter uuid := auth.uid();
  v_request public.creator_payout_requests%rowtype;
  v_amount bigint;
  v_count integer;
begin
  if v_promoter is null then
    raise exception 'Authentication required' using errcode = '28000';
  end if;
  if p_payment_method not in ('mobile_money','bank_transfer') then
    raise exception 'Unsupported payout method' using errcode = '22023';
  end if;

  if exists (
    select 1 from public.creator_payout_requests r
    where r.promoter_id = v_promoter and r.status in ('pending','approved')
  ) then
    raise exception 'An active payout request already exists' using errcode = '23505';
  end if;

  -- Lock eligible commission rows before calculating and reserving the balance.
  perform i.id
  from public.creator_order_items i
  where i.promoter_id = v_promoter
    and i.affiliate_link_id is not null
    and i.commission_status = 'payable'
    and i.commission_amount > 0
  order by i.created_at, i.id
  for update;

  select coalesce(sum(i.commission_amount), 0), count(*)
    into v_amount, v_count
  from public.creator_order_items i
  where i.promoter_id = v_promoter
    and i.affiliate_link_id is not null
    and i.commission_status = 'payable'
    and i.commission_amount > 0;

  if v_count = 0 or v_amount <= 0 then
    raise exception 'No payable commissions are available' using errcode = '22023';
  end if;

  insert into public.creator_payout_requests (promoter_id, amount, payment_method)
  values (v_promoter, v_amount, p_payment_method)
  returning * into v_request;

  insert into public.creator_payout_request_items (payout_request_id, order_item_id, commission_amount)
  select v_request.id, i.id, i.commission_amount
  from public.creator_order_items i
  where i.promoter_id = v_promoter
    and i.affiliate_link_id is not null
    and i.commission_status = 'payable'
    and i.commission_amount > 0;

  update public.creator_order_items i
  set commission_status = 'payout_requested'
  where i.promoter_id = v_promoter
    and i.affiliate_link_id is not null
    and i.commission_status = 'payable'
    and i.commission_amount > 0;

  return query select v_request.id, v_request.amount, v_request.currency,
    v_request.payment_method, v_request.status, v_request.created_at;
end;
$$;

create or replace function public.get_my_creator_payout_requests()
returns table (
  id uuid,
  amount bigint,
  currency text,
  payment_method text,
  status text,
  rejection_reason text,
  payout_reference text,
  created_at timestamptz,
  reviewed_at timestamptz,
  paid_at timestamptz
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select r.id, r.amount, r.currency, r.payment_method, r.status,
    r.rejection_reason, r.payout_reference, r.created_at, r.reviewed_at, r.paid_at
  from public.creator_payout_requests r
  where auth.uid() is not null and r.promoter_id = (select auth.uid())
  order by r.created_at desc
  limit 100;
$$;

create or replace function public.list_creator_payout_requests_admin()
returns table (
  id uuid,
  promoter_id uuid,
  amount bigint,
  currency text,
  payment_method text,
  status text,
  rejection_reason text,
  payout_reference text,
  created_at timestamptz,
  reviewed_at timestamptz,
  paid_at timestamptz
)
language plpgsql
stable
security definer
set search_path = public, pg_temp
as $$
begin
  if auth.uid() is null or not exists (
    select 1 from public.aea_marketplace_admins a
    where a.user_id = auth.uid() and a.active = true
  ) then
    raise exception 'Marketplace Admin permission required' using errcode = '42501';
  end if;

  return query
  select r.id, r.promoter_id, r.amount, r.currency, r.payment_method,
    r.status, r.rejection_reason, r.payout_reference, r.created_at,
    r.reviewed_at, r.paid_at
  from public.creator_payout_requests r
  order by case when r.status = 'pending' then 0 when r.status = 'approved' then 1 else 2 end,
    r.created_at desc
  limit 500;
end;
$$;

create or replace function public.review_creator_payout_request(
  p_request_id uuid,
  p_status text,
  p_rejection_reason text default null
)
returns table (id uuid, status text, amount bigint, currency text)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_request public.creator_payout_requests%rowtype;
  v_count integer;
begin
  if auth.uid() is null or not exists (
    select 1 from public.aea_marketplace_admins a
    where a.user_id = auth.uid() and a.active = true
  ) then
    raise exception 'Marketplace Admin permission required' using errcode = '42501';
  end if;
  if p_status not in ('approved','rejected') then
    raise exception 'Invalid payout review status' using errcode = '22023';
  end if;
  if p_status = 'rejected' and length(trim(coalesce(p_rejection_reason,''))) < 3 then
    raise exception 'A rejection reason is required' using errcode = '22023';
  end if;

  select * into v_request
  from public.creator_payout_requests r
  where r.id = p_request_id
  for update;

  if not found then
    raise exception 'Payout request not found' using errcode = 'P0002';
  end if;
  if v_request.status <> 'pending' then
    raise exception 'Only pending payout requests can be reviewed' using errcode = '22023';
  end if;

  if p_status = 'rejected' then
    update public.creator_order_items i
    set commission_status = 'payable'
    from public.creator_payout_request_items ri
    where ri.payout_request_id = v_request.id
      and i.id = ri.order_item_id
      and i.commission_status = 'payout_requested';
    get diagnostics v_count = row_count;
    if v_count <> (select count(*) from public.creator_payout_request_items ri where ri.payout_request_id = v_request.id) then
      raise exception 'Payout commission reservation is inconsistent' using errcode = '23514';
    end if;
  end if;

  update public.creator_payout_requests r
  set status = p_status,
      rejection_reason = case when p_status = 'rejected' then left(trim(p_rejection_reason),500) else null end,
      reviewed_by = auth.uid(),
      reviewed_at = now(),
      updated_at = now()
  where r.id = v_request.id
  returning r.id, r.status, r.amount, r.currency
  into id, status, amount, currency;

  return next;
end;
$$;

create or replace function public.mark_creator_payout_request_paid(
  p_request_id uuid,
  p_payout_reference text
)
returns table (id uuid, status text, amount bigint, currency text, payout_reference text, paid_at timestamptz)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_request public.creator_payout_requests%rowtype;
  v_count integer;
begin
  if auth.uid() is null or not exists (
    select 1 from public.aea_marketplace_admins a
    where a.user_id = auth.uid() and a.active = true
  ) then
    raise exception 'Marketplace Admin permission required' using errcode = '42501';
  end if;
  if length(trim(coalesce(p_payout_reference,''))) < 3 or length(trim(p_payout_reference)) > 200 then
    raise exception 'A valid transaction reference is required' using errcode = '22023';
  end if;

  select * into v_request
  from public.creator_payout_requests r
  where r.id = p_request_id
  for update;

  if not found then
    raise exception 'Payout request not found' using errcode = 'P0002';
  end if;
  if v_request.status <> 'approved' then
    raise exception 'Only approved payout requests can be marked paid' using errcode = '22023';
  end if;

  update public.creator_order_items i
  set commission_status = 'paid'
  from public.creator_payout_request_items ri
  where ri.payout_request_id = v_request.id
    and i.id = ri.order_item_id
    and i.commission_status = 'payout_requested';
  get diagnostics v_count = row_count;
  if v_count <> (select count(*) from public.creator_payout_request_items ri where ri.payout_request_id = v_request.id) then
    raise exception 'Payout commission reservation is inconsistent' using errcode = '23514';
  end if;

  update public.creator_payout_requests r
  set status = 'paid',
      payout_reference = trim(p_payout_reference),
      paid_at = now(),
      updated_at = now()
  where r.id = v_request.id
  returning r.id, r.status, r.amount, r.currency, r.payout_reference, r.paid_at
  into id, status, amount, currency, payout_reference, paid_at;

  return next;
end;
$$;

revoke all on function public.create_my_creator_payout_request(text) from public, anon;
revoke all on function public.get_my_creator_payout_requests() from public, anon;
revoke all on function public.list_creator_payout_requests_admin() from public, anon;
revoke all on function public.review_creator_payout_request(uuid,text,text) from public, anon;
revoke all on function public.mark_creator_payout_request_paid(uuid,text) from public, anon;
grant execute on function public.create_my_creator_payout_request(text) to authenticated;
grant execute on function public.get_my_creator_payout_requests() to authenticated;
grant execute on function public.list_creator_payout_requests_admin() to authenticated;
grant execute on function public.review_creator_payout_request(uuid,text,text) to authenticated;
grant execute on function public.mark_creator_payout_request_paid(uuid,text) to authenticated;
