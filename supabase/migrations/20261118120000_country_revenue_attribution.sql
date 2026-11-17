-- First-party, privacy-minimized attribution events for Country Intelligence.
-- Do not store raw contact details, message bodies, payment credentials or secrets here.

create table if not exists public.country_revenue_events (
    id uuid primary key default gen_random_uuid(),
    owner_id uuid not null references auth.users(id) on delete cascade,
    idempotency_key text not null,
    event_type text not null check (event_type in (
        'content_published', 'impression_or_view', 'tracked_click',
        'lead_captured', 'lead_qualified', 'checkout_started_or_quote_requested',
        'purchase_confirmed', 'payment_settled'
    )),
    country_code text not null check (country_code in ('TZ','KE','NG','ZA','US','UK')),
    currency text not null check (currency in ('TZS','KES','NGN','ZAR','USD','GBP')),
    campaign_id text not null,
    content_id text,
    channel text not null,
    occurred_at timestamptz not null,
    amount_minor bigint check (amount_minor is null or amount_minor >= 0),
    metadata jsonb not null default '{}'::jsonb,
    created_at timestamptz not null default now(),
    constraint country_revenue_events_owner_idempotency unique (owner_id, idempotency_key)
);

create index if not exists idx_country_revenue_events_owner_time
    on public.country_revenue_events (owner_id, occurred_at desc);
create index if not exists idx_country_revenue_events_owner_market_campaign
    on public.country_revenue_events (owner_id, country_code, campaign_id, occurred_at desc);

alter table public.country_revenue_events enable row level security;
drop policy if exists "owners can read own country revenue events" on public.country_revenue_events;
create policy "owners can read own country revenue events"
    on public.country_revenue_events for select to authenticated
    using ((select auth.uid()) = owner_id);
drop policy if exists "owners can insert own country revenue events" on public.country_revenue_events;
create policy "owners can insert own country revenue events"
    on public.country_revenue_events for insert to authenticated
    with check ((select auth.uid()) = owner_id);

revoke all on public.country_revenue_events from anon, authenticated;
grant select, insert on public.country_revenue_events to authenticated;
grant all on public.country_revenue_events to service_role;
