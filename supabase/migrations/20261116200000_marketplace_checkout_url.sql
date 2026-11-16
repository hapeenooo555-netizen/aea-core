-- Store the hosted checkout URL separately from the provider's checkout identifier.
begin;
alter table public.marketplace_orders
  add column if not exists provider_checkout_url text;
commit;
