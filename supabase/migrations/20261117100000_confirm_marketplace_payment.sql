-- Atomic, idempotent settlement transition for verified ClickPesa payments.
-- The caller must first query ClickPesa and persist the matching event in payment_events.
begin;

create or replace function public.confirm_marketplace_payment(
    p_order_id uuid,
    p_provider_payment_reference text,
    p_amount bigint,
    p_currency text default 'TZS'
) returns jsonb
language plpgsql
security definer
set search_path = public, auth, pg_temp
as $$
declare
    v_order public.marketplace_orders%rowtype;
    v_event public.payment_events%rowtype;
    v_item record;
    v_entitlements_created integer := 0;
begin
    -- This function is a privileged server operation, never a browser RPC.
    if coalesce(auth.role(), '') <> 'service_role' then
        raise exception 'Service role required' using errcode = '42501';
    end if;
    if p_order_id is null
       or nullif(btrim(coalesce(p_provider_payment_reference, '')), '') is null
       or p_amount is null or p_amount <= 0
       or upper(coalesce(p_currency, '')) <> 'TZS' then
        raise exception 'Invalid payment confirmation data' using errcode = '22023';
    end if;

    select * into v_order
    from public.marketplace_orders
    where id = p_order_id
    for update;

    if not found then
        raise exception 'Order not found' using errcode = 'P0002';
    end if;

    if v_order.provider <> 'clickpesa' then
        raise exception 'Order is not assigned to ClickPesa' using errcode = '22023';
    end if;

    if v_order.status = 'paid' then
        -- Safe retry: only accept the exact payment reference already recorded.
        if v_order.provider_checkout_id = p_provider_payment_reference then
            return jsonb_build_object(
                'order_id', v_order.id, 'status', 'paid',
                'reused', true, 'entitlements_granted', 0
            );
        end if;
        raise exception 'Order already paid with a different payment reference' using errcode = '23505';
    end if;

    if v_order.status <> 'pending' or (v_order.expires_at is not null and v_order.expires_at < now()) then
        raise exception 'Order is not payable' using errcode = '22023';
    end if;

    if v_order.total_amount <> p_amount or v_order.currency <> upper(p_currency) then
        raise exception 'Payment does not match order total and currency' using errcode = '22023';
    end if;

    select * into v_event
    from public.payment_events
    where provider = 'clickpesa'
      and provider_event_id = p_provider_payment_reference
      and order_id = p_order_id
    for update;

    if not found then
        raise exception 'Verified provider event was not recorded' using errcode = 'P0002';
    end if;

    if v_event.verification_status = 'rejected' then
        raise exception 'Provider event was rejected' using errcode = '22023';
    end if;

    if v_event.amount is not null and v_event.amount <> p_amount then
        raise exception 'Webhook amount does not match verified payment' using errcode = '22023';
    end if;
    if v_event.currency is not null and upper(v_event.currency) <> upper(p_currency) then
        raise exception 'Webhook currency does not match verified payment' using errcode = '22023';
    end if;

    update public.marketplace_orders
    set status = 'paid',
        provider_checkout_id = p_provider_payment_reference,
        paid_at = now(),
        updated_at = now()
    where id = p_order_id;

    for v_item in
        select oi.id, oi.product_id
        from public.marketplace_order_items oi
        where oi.order_id = p_order_id
    loop
        insert into public.product_entitlements (
            order_item_id, order_id, product_id, buyer_id, seller_id, status
        ) values (
            v_item.id, p_order_id, v_item.product_id,
            v_order.buyer_id, v_order.seller_id, 'active'
        )
        on conflict (order_item_id) do nothing;

        if found then
            v_entitlements_created := v_entitlements_created + 1;
        end if;
    end loop;

    insert into public.seller_ledger_entries (
        seller_id, order_id, entry_type, amount, currency, idempotency_key, metadata
    ) values (
        v_order.seller_id, p_order_id, 'sale_gross', v_order.total_amount,
        v_order.currency, 'sale:' || p_order_id::text,
        jsonb_build_object('provider', 'clickpesa',
                           'provider_payment_reference', p_provider_payment_reference)
    ) on conflict (idempotency_key) do nothing;

    update public.payment_events
    set verification_status = 'verified', processed_at = now()
    where id = v_event.id;

    return jsonb_build_object(
        'order_id', p_order_id,
        'status', 'paid',
        'reused', false,
        'entitlements_granted', v_entitlements_created
    );
end;
$$;

revoke all on function public.confirm_marketplace_payment(uuid, text, bigint, text) from public, anon, authenticated;
grant execute on function public.confirm_marketplace_payment(uuid, text, bigint, text) to service_role;

commit;
