-- Cover the two new creator order item foreign keys for attribution lookups.
create index if not exists idx_creator_order_items_affiliate_link
  on public.creator_order_items(affiliate_link_id);
create index if not exists idx_creator_order_items_promoter
  on public.creator_order_items(promoter_id);
