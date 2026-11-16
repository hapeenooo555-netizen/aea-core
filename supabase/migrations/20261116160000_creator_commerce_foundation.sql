-- Creator commerce foundation: immutable order snapshots and server-verified payment records.
-- No live payment provider is enabled by this migration.
begin;

create table if not exists public.marketplace_orders (
    id uuid primary key default gen_random_uuid(),
    buyer_id uuid not null references auth.users(id) on delete restrict,
    seller_id uuid not null references auth.users(id) on delete restrict,
    status text not null default 'pending'
        check (status in ('pending','paid','cancelled','expired','refunded','partially_refunded')),
    currency text not null default 'TZS' check (currency = 'TZS'),
    subtotal_amount bigint not null check (subtotal_amount > 0),
    discount_amount bigint not null default 0 check (discount_amount >= 0),
    total_amount bigint not null check (total_amount > 0),
    provider text not null default 'test' check (provider in ('test','clickpesa')),
    provider_checkout_id text,
    idempotency_key text not null,
    expires_at timestamptz,
    paid_at timestamptz,
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    unique (buyer_id, idempotency_key),
    check (discount_amount <= subtotal_amount),
    check (total_amount = subtotal_amount - discount_amount)
);

create index if not exists marketplace_orders_buyer_created_idx
    on public.marketplace_orders (buyer_id, created_at desc);
create index if not exists marketplace_orders_seller_created_idx
    on public.marketplace_orders (seller_id, created_at desc);
create index if not exists marketplace_orders_status_created_idx
    on public.marketplace_orders (status, created_at);

create table if not exists public.marketplace_order_items (
    id uuid primary key default gen_random_uuid(),
    order_id uuid not null references public.marketplace_orders(id) on delete restrict,
    product_id uuid not null references public.creator_products(id) on delete restrict,
    product_title text not null,
    product_description text not null,
    product_type text not null,
    unit_amount bigint not null check (unit_amount > 0),
    quantity integer not null default 1 check (quantity = 1),
    currency text not null default 'TZS' check (currency = 'TZS'),
    created_at timestamptz not null default now()
);
create index if not exists marketplace_order_items_order_idx
    on public.marketplace_order_items (order_id);

create table if not exists public.payment_events (
    id uuid primary key default gen_random_uuid(),
    order_id uuid not null references public.marketplace_orders(id) on delete restrict,
    provider text not null check (provider in ('test','clickpesa')),
    provider_event_id text not null,
    event_type text not null,
    verification_status text not null default 'received'
        check (verification_status in ('received','verified','rejected')),
    amount bigint check (amount is null or amount >= 0),
    currency text,
    payload_sha256 text not null,
    processed_at timestamptz,
    created_at timestamptz not null default now(),
    unique (provider, provider_event_id)
);
create index if not exists payment_events_order_created_idx
    on public.payment_events (order_id, created_at desc);

create table if not exists public.product_entitlements (
    id uuid primary key default gen_random_uuid(),
    order_item_id uuid not null unique references public.marketplace_order_items(id) on delete restrict,
    order_id uuid not null references public.marketplace_orders(id) on delete restrict,
    product_id uuid not null references public.creator_products(id) on delete restrict,
    buyer_id uuid not null references auth.users(id) on delete restrict,
    seller_id uuid not null references auth.users(id) on delete restrict,
    status text not null default 'active' check (status in ('active','revoked')),
    granted_at timestamptz not null default now(),
    revoked_at timestamptz,
    created_at timestamptz not null default now()
);
create index if not exists product_entitlements_buyer_idx
    on public.product_entitlements (buyer_id, created_at desc);

create table if not exists public.seller_ledger_entries (
    id uuid primary key default gen_random_uuid(),
    seller_id uuid not null references auth.users(id) on delete restrict,
    order_id uuid references public.marketplace_orders(id) on delete restrict,
    entry_type text not null check (entry_type in ('sale_gross','provider_fee','aea_fee','refund','chargeback','payout')),
    amount bigint not null,
    currency text not null default 'TZS' check (currency = 'TZS'),
    idempotency_key text not null unique,
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now()
);
create index if not exists seller_ledger_entries_seller_created_idx
    on public.seller_ledger_entries (seller_id, created_at desc);

alter table public.marketplace_orders enable row level security;
alter table public.marketplace_order_items enable row level security;
alter table public.payment_events enable row level security;
alter table public.product_entitlements enable row level security;
alter table public.seller_ledger_entries enable row level security;

-- Buyers and sellers may read their own order. Mutations remain trusted-backend only.
drop policy if exists marketplace_orders_select_participant on public.marketplace_orders;
create policy marketplace_orders_select_participant on public.marketplace_orders
    for select to authenticated
    using (auth.uid() = buyer_id or auth.uid() = seller_id);

drop policy if exists marketplace_order_items_select_participant on public.marketplace_order_items;
create policy marketplace_order_items_select_participant on public.marketplace_order_items
    for select to authenticated
    using (exists (
        select 1 from public.marketplace_orders o
        where o.id = order_id and (o.buyer_id = auth.uid() or o.seller_id = auth.uid())
    ));

-- Payment event payloads and status transitions are private to trusted service-role code.
-- No authenticated-user policy is intentionally created for payment_events.

drop policy if exists product_entitlements_select_buyer on public.product_entitlements;
create policy product_entitlements_select_buyer on public.product_entitlements
    for select to authenticated
    using (auth.uid() = buyer_id);

drop policy if exists seller_ledger_entries_select_seller on public.seller_ledger_entries;
create policy seller_ledger_entries_select_seller on public.seller_ledger_entries
    for select to authenticated
    using (auth.uid() = seller_id);

commit;
