-- Add Airtel Money as a first-class Marketplace payment provider.
-- Preserve ClickPesa and keep confirmation restricted to trusted service-role code.
begin;

alter table public.marketplace_orders
  drop constraint if exists marketplace_orders_provider_check;
alter table public.marketplace_orders
  add constraint marketplace_orders_provider_check
  check (provider in ('test', 'clickpesa', 'airtel'));

alter table public.payment_events
  drop constraint if exists payment_events_provider_check;
alter table public.payment_events
  add constraint payment_events_provider_check
  check (provider in ('test', 'clickpesa', 'airtel'));

create or replace function public.confirm_marketplace_payment(
  p_order_id uuid,
  p_provider_event_id text,
  p_amount bigint,
  p_currency text
) returns jsonb
language plpgsql
security definer
set search_path = public, auth, pg_temp
as $$
declare
  v_order public.marketplace_orders%rowtype;
  v_event public.payment_events%rowtype;
  v_item public.marketplace_order_items%rowtype;
  v_granted integer := 0;
begin
  if p_order_id is null
     or nullif(btrim(coalesce(p_provider_event_id, '')), '') is null
     or p_amount is null or p_amount <= 0
     or upper(coalesce(p_currency, '')) <> 'TZS' then
    raise exception 'Invalid payment confirmation input' using errcode = '22023';
  end if;

  select * into v_order
    from public.marketplace_orders
   where id = p_order_id
   for update;

  if not found then
    raise exception 'Order not found' using errcode = 'P0002';
  end if;

  select * into v_event
    from public.payment_events
   where provider = v_order.provider
     and provider_event_id = btrim(p_provider_event_id)
     and order_id = p_order_id
   for update;

  if not found then
    raise exception 'Verified provider event not found' using errcode = 'P0002';
  end if;

  if v_order.provider not in ('clickpesa', 'airtel')
     or v_order.currency <> 'TZS'
     or v_order.total_amount <> p_amount
     or v_event.provider <> v_order.provider
     or v_event.amount <> p_amount
     or upper(coalesce(v_event.currency, '')) <> 'TZS'
     or v_event.event_type <> 'PAYMENT RECEIVED'
     or v_event.verification_status = 'rejected' then
    update public.payment_events
       set verification_status = 'rejected', processed_at = now()
     where id = v_event.id and verification_status <> 'verified';
    return jsonb_build_object('confirmed', false, 'reason', 'payment_mismatch',
                              'order_id', p_order_id, 'status', v_order.status);
  end if;

  if v_order.status = 'paid' then
    if v_order.provider_payment_reference = btrim(p_provider_event_id) then
      return jsonb_build_object('confirmed', true, 'already_processed', true,
                                'order_id', v_order.id, 'status', 'paid');
    end if;
    raise exception 'Order already paid with a different provider reference'
      using errcode = '23505';
  end if;

  if v_order.status <> 'pending' or (v_order.expires_at is not null and v_order.expires_at < now()) then
    update public.payment_events
       set verification_status = 'rejected', processed_at = now()
     where id = v_event.id;
    return jsonb_build_object('confirmed', false, 'reason', 'order_not_payable',
                              'order_id', p_order_id, 'status', v_order.status);
  end if;

  update public.marketplace_orders
     set status = 'paid',
         paid_at = now(),
         provider_payment_reference = btrim(p_provider_event_id),
         updated_at = now()
   where id = p_order_id;

  update public.payment_events
     set verification_status = 'verified', processed_at = now()
   where id = v_event.id;

  for v_item in
    select * from public.marketplace_order_items where order_id = p_order_id
  loop
    insert into public.product_entitlements
      (order_item_id, order_id, product_id, buyer_id, seller_id)
    values
      (v_item.id, p_order_id, v_item.product_id, v_order.buyer_id, v_order.seller_id)
    on conflict (order_item_id) do nothing;

    if found then
      v_granted := v_granted + 1;
    end if;
  end loop;

  insert into public.seller_ledger_entries
    (seller_id, order_id, entry_type, amount, currency, idempotency_key, metadata)
  values
    (v_order.seller_id, p_order_id, 'sale_gross', v_order.total_amount,
     'TZS', 'sale_gross:' || p_order_id::text,
     jsonb_build_object('provider', v_order.provider,
                        'provider_payment_reference', btrim(p_provider_event_id)))
  on conflict (idempotency_key) do nothing;

  return jsonb_build_object('confirmed', true, 'already_processed', false,
                            'order_id', p_order_id, 'status', 'paid',
                            'entitlements_granted', v_granted);
end;
$$;

revoke all on function public.confirm_marketplace_payment(uuid, text, bigint, text)
  from public, anon, authenticated;
grant execute on function public.confirm_marketplace_payment(uuid, text, bigint, text)
  to service_role;

commit;
