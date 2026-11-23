-- Vendor-approved affiliate commission terms, snapshotted at purchase intent.
-- Commission is only payable after the provider settlement RPC confirms payment.

alter table public.creator_promotion_requests
  add column if not exists commission_rate_bps integer;

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'creator_promotion_requests_commission_rate_check'
      and conrelid = 'public.creator_promotion_requests'::regclass
  ) then
    alter table public.creator_promotion_requests
      add constraint creator_promotion_requests_commission_rate_check
      check (commission_rate_bps is null or commission_rate_bps between 1 and 10000);
  end if;
end;
$$;

-- Vendor can approve/reject and set the rate in the same row update.
-- Existing promoter RLS still restricts promoter updates to withdrawal only.
revoke update on public.creator_promotion_requests from authenticated;
grant update (status, commission_rate_bps) on public.creator_promotion_requests to authenticated;

create or replace function public.create_creator_affiliate_link(p_request_id uuid, p_code text)
returns setof public.creator_affiliate_links
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_request public.creator_promotion_requests%rowtype;
    v_product public.creator_products%rowtype;
    v_listing public.creator_marketplace_listings%rowtype;
begin
    if auth.uid() is null or p_code is null or length(p_code) < 20 or length(p_code) > 80
       or p_code !~ '^[A-Za-z0-9_-]+$' then
        raise exception 'Invalid affiliate link request' using errcode = '42501';
    end if;
    select * into v_request from public.creator_promotion_requests
     where id = p_request_id and promoter_id = auth.uid()
       and status = 'approved' and commission_rate_bps between 1 and 10000;
    if not found then
        raise exception 'Approved promotion request with vendor commission terms not found' using errcode = '42501';
    end if;
    select * into v_product from public.creator_products
     where id = v_request.product_id and owner_id = v_request.owner_id
       and status = 'listed' and marketplace_enabled = true
       and completion_status = 'finished';
    if not found then
        raise exception 'Product is not available for promotion' using errcode = '23514';
    end if;
    select * into v_listing from public.creator_marketplace_listings
     where id = v_request.listing_id and product_id = v_request.product_id
       and owner_id = v_request.owner_id and status = 'approved';
    if not found then
        raise exception 'Marketplace listing is not approved' using errcode = '23514';
    end if;
    return query
      insert into public.creator_affiliate_links
        (product_id, listing_id, owner_id, promoter_id, promotion_request_id, referral_code)
      values
        (v_request.product_id, v_request.listing_id, v_request.owner_id, v_request.promoter_id, v_request.id, p_code)
      on conflict (promotion_request_id) do update
        set referral_code = case
          when creator_affiliate_links.status = 'active' then creator_affiliate_links.referral_code
          else excluded.referral_code
        end,
        status = 'active', revoked_at = null
      returning *;
end;
$$;
revoke all on function public.create_creator_affiliate_link(uuid,text) from public, anon;
grant execute on function public.create_creator_affiliate_link(uuid,text) to authenticated, service_role;

create or replace function public.resolve_creator_affiliate_attribution(
  p_code text,
  p_product_id uuid
) returns table(affiliate_link_id uuid, promoter_id uuid, commission_rate_bps integer)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select l.id, l.promoter_id, pr.commission_rate_bps
    from public.creator_affiliate_links l
    join public.creator_products p on p.id = l.product_id
    join public.creator_marketplace_listings ml on ml.id = l.listing_id
    join public.creator_promotion_requests pr on pr.id = l.promotion_request_id
   where l.referral_code = p_code
     and l.product_id = p_product_id
     and l.status = 'active'
     and p.status = 'listed'
     and p.marketplace_enabled = true
     and p.completion_status = 'finished'
     and ml.status = 'approved'
     and pr.status = 'approved'
     and pr.commission_rate_bps between 1 and 10000
     and pr.promoter_id = l.promoter_id
     and l.owner_id = p.owner_id
   limit 1;
$$;
revoke all on function public.resolve_creator_affiliate_attribution(text,uuid) from public, anon, authenticated;
grant execute on function public.resolve_creator_affiliate_attribution(text,uuid) to service_role;

create or replace function public.resolve_creator_affiliate_link(p_code text)
returns table(product_slug text)
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
    v_link public.creator_affiliate_links%rowtype;
    v_slug text;
begin
    if p_code is null or length(p_code) < 20 or length(p_code) > 80 then
        return;
    end if;
    select l.* into v_link
      from public.creator_affiliate_links l
      join public.creator_products p on p.id = l.product_id
      join public.creator_marketplace_listings ml on ml.id = l.listing_id
      join public.creator_promotion_requests pr on pr.id = l.promotion_request_id
     where l.referral_code = p_code
       and l.status = 'active'
       and p.status = 'listed'
       and p.marketplace_enabled = true
       and p.completion_status = 'finished'
       and ml.status = 'approved'
       and pr.status = 'approved'
       and pr.commission_rate_bps between 1 and 10000
       and pr.promoter_id = l.promoter_id
       and l.owner_id = p.owner_id
     limit 1;
    if not found then
        return;
    end if;
    select slug into v_slug from public.creator_products where id = v_link.product_id;
    insert into public.creator_affiliate_clicks(affiliate_link_id) values (v_link.id);
    return query select v_slug;
end;
$$;
revoke all on function public.resolve_creator_affiliate_link(text) from public;
grant execute on function public.resolve_creator_affiliate_link(text) to anon, authenticated;

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
  if p_provider is null or char_length(p_provider) not between 2 and 40
    or p_provider_event_id is null or char_length(p_provider_event_id) not between 1 and 200
    or p_provider_reference is null or btrim(p_provider_reference) = ''
    or p_event_type <> 'payment.succeeded' then
    raise exception 'invalid successful payment event' using errcode = '22023';
  end if;
  if p_amount <> v_order.total_amount or p_currency <> v_order.currency then
    raise exception 'payment amount or currency mismatch' using errcode = '22023';
  end if;
  select * into v_existing from public.creator_payment_events
    where provider = p_provider and provider_event_id = p_provider_event_id;
  if found then
    if v_existing.order_id <> p_order_id
      or v_existing.provider_reference is distinct from p_provider_reference
      or v_existing.event_type <> p_event_type
      or v_existing.amount <> p_amount or v_existing.currency <> p_currency then
      raise exception 'provider event replay does not match this order' using errcode = '22023';
    end if;
    return jsonb_build_object('success', true, 'duplicate', true, 'order_id', v_order.id);
  end if;
  if v_order.status <> 'pending' then
    raise exception 'order is not payable or was already settled by another event' using errcode = '22023';
  end if;
  insert into public.creator_payment_events(order_id, provider, provider_event_id, provider_reference, event_type, amount, currency)
  values (p_order_id, p_provider, p_provider_event_id, p_provider_reference, p_event_type, p_amount, p_currency)
  returning id into v_event_id;
  update public.creator_orders set status = 'paid', paid_at = now(), updated_at = now() where id = p_order_id;
  insert into public.creator_product_entitlements(order_item_id, order_id, buyer_id, product_id, product_file_id)
  select i.id, i.order_id, v_order.buyer_id, i.product_id, i.product_file_id
  from public.creator_order_items i where i.order_id = p_order_id
  on conflict (order_item_id) do nothing;
  update public.creator_order_items
     set commission_status = case
       when commission_rate_bps is not null and commission_amount > 0 then 'payable'
       when commission_rate_bps is not null then 'approved'
       else commission_status
     end
   where order_id = p_order_id and affiliate_link_id is not null;
  return jsonb_build_object('success', true, 'duplicate', false, 'order_id', v_order.id);
end;
$$;
revoke all on function public.settle_creator_order(uuid,text,text,text,text,bigint,text) from public, anon, authenticated;
grant execute on function public.settle_creator_order(uuid,text,text,text,text,bigint,text) to service_role;
