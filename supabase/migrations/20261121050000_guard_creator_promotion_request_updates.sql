-- Lock promotion-request identity and constrain each participant's state transition.
create or replace function public.guard_creator_promotion_request_update()
returns trigger
language plpgsql
set search_path = ''
as $$
begin
  if new.listing_id is distinct from old.listing_id
     or new.product_id is distinct from old.product_id
     or new.owner_id is distinct from old.owner_id
     or new.promoter_id is distinct from old.promoter_id
     or new.message is distinct from old.message
     or new.created_at is distinct from old.created_at then
    raise exception 'Promotion request identity and message are immutable';
  end if;

  if (select auth.uid()) = old.owner_id then
    if old.status <> 'pending' or new.status not in ('approved', 'rejected') then
      raise exception 'Vendor may only approve or reject a pending request';
    end if;
  elsif (select auth.uid()) = old.promoter_id then
    if old.status <> 'pending' or new.status <> 'withdrawn' then
      raise exception 'Promoter may only withdraw a pending request';
    end if;
  else
    raise exception 'Only request participants may update this request';
  end if;

  new.updated_at = now();
  return new;
end;
$$;

create trigger creator_promotion_request_update_guard
before update on public.creator_promotion_requests
for each row execute function public.guard_creator_promotion_request_update();
