-- Order, payment-event and entitlement foundation for secure creator commerce.
-- No public endpoint can mark an order paid. Only the service-role settlement RPC
-- (called after provider signature/amount/currency verification) can issue access.

create table if not exists public.creator_orders (
  id uuid primary key default gen_random_uuid(),
  buyer_id uuid not null references auth.users(id) on delete restrict,
  seller_id uuid not null references auth.users(id) on delete restrict,
  currency text not null check (currency = 'TZS'),
  subtotal_amount bigint not null check (subtotal_amount > 0),
  total_amount bigint not null check (total_amount = subtotal_amount),
  status text not null default 'pending' check (status in ('pending','paid','failed','refunded','cancelled')),
  idempotency_key text not null check (char_length(idempotency_key) between 8 and 128),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  paid_at timestamptz,
  unique (buyer_id, idempotency_key)
);
create index if not exists idx_creator_orders_buyer_created on public.creator_orders(buyer_id, created_at desc);
create index if not exists idx_creator_orders_seller_created on public.creator_orders(seller_id, created_at desc);

create table if not exists public.creator_order_items (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references public.creator_orders(id) on delete cascade,
  product_id uuid not null references public.creator_products(id) on delete restrict,
  product_file_id uuid not null references public.creator_product_files(id) on delete restrict,
  seller_id uuid not null references auth.users(id) on delete restrict,
  product_title text not null,
  product_type text not null,
  unit_amount bigint not null check (unit_amount > 0),
  currency text not null check (currency = 'TZS'),
  created_at timestamptz not null default now(),
  unique (order_id, product_id)
);
create index if not exists idx_creator_order_items_order on public.creator_order_items(order_id);

create table if not exists public.creator_payment_events (
  id uuid primary key default gen_random_uuid(),
  order_id uuid not null references public.creator_orders(id) on delete restrict,
  provider text not null check (char_length(provider) between 2 and 40),
  provider_event_id text not null check (char_length(provider_event_id) between 1 and 200),
  provider_reference text,
  event_type text not null,
  amount bigint not null check (amount > 0),
  currency text not null check (currency = 'TZS'),
  created_at timestamptz not null default now(),
  unique (provider, provider_event_id)
);

create table if not exists public.creator_product_entitlements (
  id uuid primary key default gen_random_uuid(),
  order_item_id uuid not null unique references public.creator_order_items(id) on delete restrict,
  order_id uuid not null references public.creator_orders(id) on delete restrict,
  buyer_id uuid not null references auth.users(id) on delete restrict,
  product_id uuid not null references public.creator_products(id) on delete restrict,
  product_file_id uuid not null references public.creator_product_files(id) on delete restrict,
  granted_at timestamptz not null default now(),
  revoked_at timestamptz
);
create index if not exists idx_creator_entitlements_buyer on public.creator_product_entitlements(buyer_id, granted_at desc);

alter table public.creator_orders enable row level security;
alter table public.creator_order_items enable row level security;
alter table public.creator_payment_events enable row level security;
alter table public.creator_product_entitlements enable row level security;

drop policy if exists "buyers and sellers read own creator orders" on public.creator_orders;
create policy "buyers and sellers read own creator orders" on public.creator_orders
 for select to authenticated using ((select auth.uid()) = buyer_id or (select auth.uid()) = seller_id);
drop policy if exists "buyers and sellers read own creator order items" on public.creator_order_items;
create policy "buyers and sellers read own creator order items" on public.creator_order_items
 for select to authenticated using (exists (
   select 1 from public.creator_orders o where o.id = order_id
   and ((select auth.uid()) = o.buyer_id or (select auth.uid()) = o.seller_id)
 ));
drop policy if exists "buyers read own payment events" on public.creator_payment_events;
create policy "buyers read own payment events" on public.creator_payment_events
 for select to authenticated using (exists (
   select 1 from public.creator_orders o where o.id = order_id and (select auth.uid()) = o.buyer_id
 ));
drop policy if exists "buyers read own entitlements" on public.creator_product_entitlements;
create policy "buyers read own entitlements" on public.creator_product_entitlements
 for select to authenticated using ((select auth.uid()) = buyer_id);

revoke all on public.creator_orders, public.creator_order_items, public.creator_payment_events, public.creator_product_entitlements from anon, authenticated;
grant select on public.creator_orders, public.creator_order_items, public.creator_payment_events, public.creator_product_entitlements to authenticated;
grant all on public.creator_orders, public.creator_order_items, public.creator_payment_events, public.creator_product_entitlements to service_role;

create or replace function public.settle_creator_order(
  p_order_id uuid,
  p_provider text,
  p_provider_event_id text,
  p_provider_reference text,
  p_event_type text,
  p_amount bigint,
  p_currency text
) returns jsonb
language plpgsql
security definer
set search_path = public
as $$
declare
  v_order public.creator_orders%rowtype;
  v_event_id uuid;
  v_existing public.creator_payment_events%rowtype;
begin
  if auth.role() <> 'service_role' then
    raise exception 'service role required' using errcode = '42501';
  end if;
  select * into v_order from public.creator_orders where id = p_order_id for update;
  if not found then raise exception 'order not found' using errcode = 'P0002'; end if;
  if p_amount <> v_order.total_amount or p_currency <> v_order.currency then
    raise exception 'payment amount or currency mismatch' using errcode = '22023';
  end if;
  if v_order.status = 'paid' then
    return jsonb_build_object('success', true, 'duplicate', true, 'order_id', v_order.id);
  end if;
  if v_order.status <> 'pending' then
    raise exception 'order is not payable' using errcode = '22023';
  end if;
  insert into public.creator_payment_events(order_id, provider, provider_event_id, provider_reference, event_type, amount, currency)
  values (p_order_id, p_provider, p_provider_event_id, p_provider_reference, p_event_type, p_amount, p_currency)
  on conflict (provider, provider_event_id) do nothing
  returning id into v_event_id;
  if v_event_id is null then
    select * into v_existing from public.creator_payment_events
      where provider = p_provider and provider_event_id = p_provider_event_id;
    if not found or v_existing.order_id <> p_order_id
      or v_existing.amount <> p_amount or v_existing.currency <> p_currency then
      raise exception 'provider event replay does not match this order' using errcode = '22023';
    end if;
    return jsonb_build_object('success', true, 'duplicate', true, 'order_id', v_order.id);
  end if;
  update public.creator_orders set status = 'paid', paid_at = now(), updated_at = now() where id = p_order_id;
  insert into public.creator_product_entitlements(order_item_id, order_id, buyer_id, product_id, product_file_id)
  select i.id, i.order_id, v_order.buyer_id, i.product_id, i.product_file_id
  from public.creator_order_items i where i.order_id = p_order_id
  on conflict (order_item_id) do nothing;
  return jsonb_build_object('success', true, 'duplicate', false, 'order_id', v_order.id);
end;
$$;
revoke all on function public.settle_creator_order(uuid,text,text,text,text,bigint,text) from public, anon, authenticated;
grant execute on function public.settle_creator_order(uuid,text,text,text,text,bigint,text) to service_role;
