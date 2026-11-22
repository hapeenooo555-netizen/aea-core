-- First-party AEA affiliate links and privacy-minimal click attribution.
-- Links can only be issued for an approved promotion request.
create table if not exists public.creator_affiliate_links (
    id uuid primary key default gen_random_uuid(),
    product_id uuid not null references public.creator_products(id) on delete cascade,
    listing_id uuid not null references public.creator_marketplace_listings(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    promoter_id uuid not null references auth.users(id) on delete cascade,
    promotion_request_id uuid not null references public.creator_promotion_requests(id) on delete cascade,
    referral_code text not null unique check (referral_code ~ '^[A-Za-z0-9_-]{20,80}$'),
    status text not null default 'active' check (status in ('active','revoked')),
    created_at timestamptz not null default now(),
    revoked_at timestamptz,
    constraint creator_affiliate_links_not_self check (owner_id <> promoter_id),
    constraint creator_affiliate_links_request_unique unique (promotion_request_id)
);
create index if not exists idx_creator_affiliate_links_promoter
  on public.creator_affiliate_links(promoter_id, created_at desc);
create index if not exists idx_creator_affiliate_links_product
  on public.creator_affiliate_links(product_id, status);
alter table public.creator_affiliate_links enable row level security;
drop policy if exists "affiliate participants read own links" on public.creator_affiliate_links;
create policy "affiliate participants read own links"
  on public.creator_affiliate_links for select to authenticated
  using ((select auth.uid()) = promoter_id or (select auth.uid()) = owner_id);
revoke all on public.creator_affiliate_links from anon, authenticated;
grant select on public.creator_affiliate_links to authenticated;
grant insert, update, delete on public.creator_affiliate_links to service_role;

create table if not exists public.creator_affiliate_clicks (
    id bigint generated always as identity primary key,
    affiliate_link_id uuid not null references public.creator_affiliate_links(id) on delete cascade,
    clicked_at timestamptz not null default now()
);
create index if not exists idx_creator_affiliate_clicks_link_time
  on public.creator_affiliate_clicks(affiliate_link_id, clicked_at desc);
alter table public.creator_affiliate_clicks enable row level security;
revoke all on public.creator_affiliate_clicks from anon, authenticated;
grant select on public.creator_affiliate_clicks to service_role;

-- Public redirect resolver: logs only the link and timestamp, not IP address or
-- raw user-agent. The high-entropy code is the public capability token.
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
     where l.referral_code = p_code
       and l.status = 'active'
       and p.status = 'listed'
       and p.marketplace_enabled = true
       and ml.status = 'approved'
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
