-- Promoters can read a minimal, self-scoped earnings ledger without gaining
-- direct SELECT access to buyer/seller order rows or product file metadata.

create or replace function public.get_my_creator_affiliate_earnings()
returns table (
  order_id uuid,
  product_id uuid,
  product_title text,
  product_type text,
  unit_amount bigint,
  currency text,
  commission_rate_bps integer,
  commission_amount bigint,
  commission_status text,
  created_at timestamptz
)
language sql
stable
security definer
set search_path = public, pg_temp
as $$
  select
    i.order_id,
    i.product_id,
    i.product_title,
    i.product_type,
    i.unit_amount,
    i.currency,
    i.commission_rate_bps,
    i.commission_amount,
    i.commission_status,
    i.created_at
  from public.creator_order_items i
  where auth.uid() is not null
    and i.promoter_id = (select auth.uid())
    and i.affiliate_link_id is not null
  order by i.created_at desc
  limit 500;
$$;

revoke all on function public.get_my_creator_affiliate_earnings() from public, anon;
grant execute on function public.get_my_creator_affiliate_earnings() to authenticated;

create index if not exists creator_order_items_promoter_created_idx
  on public.creator_order_items (promoter_id, created_at desc)
  where promoter_id is not null;
