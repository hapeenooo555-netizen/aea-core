-- Explicit product completion state; old listings are not silently certified.
alter table public.creator_products
  add column if not exists completion_status text not null default 'draft'
  check (completion_status in ('draft', 'finished'));

create index if not exists idx_creator_products_completion_status
  on public.creator_products(completion_status, status);
