-- Persist affiliate attribution on purchased order items without inventing a commission rate.
-- Commission remains unconfigured until vendor-approved terms exist.
alter table public.creator_order_items
  add column if not exists affiliate_link_id uuid references public.creator_affiliate_links(id) on delete set null,
  add column if not exists promoter_id uuid references auth.users(id) on delete set null,
  add column if not exists commission_rate_bps integer,
  add column if not exists commission_amount bigint,
  add column if not exists commission_status text not null default 'not_applicable';

do $$
begin
  if not exists (
    select 1 from pg_constraint
    where conname = 'creator_order_items_affiliate_pair_check'
      and conrelid = 'public.creator_order_items'::regclass
  ) then
    alter table public.creator_order_items
      add constraint creator_order_items_affiliate_pair_check
      check ((affiliate_link_id is null) = (promoter_id is null));
  end if;
  if not exists (
    select 1 from pg_constraint
    where conname = 'creator_order_items_commission_rate_check'
      and conrelid = 'public.creator_order_items'::regclass
  ) then
    alter table public.creator_order_items
      add constraint creator_order_items_commission_rate_check
      check (commission_rate_bps is null or commission_rate_bps between 0 and 10000);
  end if;
  if not exists (
    select 1 from pg_constraint
    where conname = 'creator_order_items_commission_amount_check'
      and conrelid = 'public.creator_order_items'::regclass
  ) then
    alter table public.creator_order_items
      add constraint creator_order_items_commission_amount_check
      check (commission_amount is null or commission_amount >= 0);
  end if;
  if not exists (
    select 1 from pg_constraint
    where conname = 'creator_order_items_commission_status_check'
      and conrelid = 'public.creator_order_items'::regclass
  ) then
    alter table public.creator_order_items
      add constraint creator_order_items_commission_status_check
      check (commission_status in ('not_applicable','rate_unconfigured','pending','approved','payable','void','paid'));
  end if;
end;
$$;

create index if not exists idx_creator_affiliate_links_listing on public.creator_affiliate_links(listing_id);
create index if not exists idx_creator_affiliate_links_owner on public.creator_affiliate_links(owner_id);
create index if not exists idx_creator_order_items_product on public.creator_order_items(product_id);
create index if not exists idx_creator_order_items_product_file on public.creator_order_items(product_file_id);
create index if not exists idx_creator_order_items_seller on public.creator_order_items(seller_id);
create index if not exists idx_creator_payment_events_order on public.creator_payment_events(order_id);
create index if not exists idx_creator_entitlements_order on public.creator_product_entitlements(order_id);
create index if not exists idx_creator_entitlements_product on public.creator_product_entitlements(product_id);
create index if not exists idx_creator_entitlements_product_file on public.creator_product_entitlements(product_file_id);
create index if not exists idx_creator_product_files_product on public.creator_product_files(product_id);

-- Resolve attribution only for a live link tied to this exact approved product and promoter.
-- This is a service-role-only lookup; the buyer cannot choose a promoter UUID or commission.
create or replace function public.resolve_creator_affiliate_attribution(
  p_code text,
  p_product_id uuid
) returns table(affiliate_link_id uuid, promoter_id uuid)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select l.id, l.promoter_id
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
     and pr.promoter_id = l.promoter_id
     and l.owner_id = p.owner_id
   limit 1;
$$;
revoke all on function public.resolve_creator_affiliate_attribution(text,uuid) from public, anon, authenticated;
grant execute on function public.resolve_creator_affiliate_attribution(text,uuid) to service_role;

-- The link resolver is intentionally public for the click redirect, but a vendor
-- revoking a promotion request immediately invalidates the referral destination.
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
       and pr.promoter_id = l.promoter_id
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

-- No anonymous caller may mint a referral link or probe private attribution validity.
revoke all on function public.create_creator_affiliate_link(uuid,text) from public, anon;
grant execute on function public.create_creator_affiliate_link(uuid,text) to authenticated, service_role;
revoke all on function public.validate_creator_affiliate_link(text,uuid,uuid) from public, anon;
grant execute on function public.validate_creator_affiliate_link(text,uuid,uuid) to authenticated, service_role;
