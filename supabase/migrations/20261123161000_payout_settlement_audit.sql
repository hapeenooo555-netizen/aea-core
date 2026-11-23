alter table public.creator_payout_requests
  add column if not exists paid_by uuid references auth.users(id) on delete restrict;

create unique index if not exists creator_payout_requests_reference_unique_idx
  on public.creator_payout_requests (payout_reference)
  where payout_reference is not null;

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
      paid_by = auth.uid(),
      paid_at = now(),
      updated_at = now()
  where r.id = v_request.id
  returning r.id, r.status, r.amount, r.currency, r.payout_reference, r.paid_at
  into id, status, amount, currency, payout_reference, paid_at;

  return next;
end;
$$;

revoke all on function public.mark_creator_payout_request_paid(uuid,text) from public, anon;
grant execute on function public.mark_creator_payout_request_paid(uuid,text) to authenticated;
