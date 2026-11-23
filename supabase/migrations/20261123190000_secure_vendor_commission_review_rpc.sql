-- Restrict commission rate changes to a vendor-ownership-checked review RPC.
revoke update on public.creator_promotion_requests from authenticated;
grant update (status) on public.creator_promotion_requests to authenticated;
drop policy if exists "vendors review promotion requests" on public.creator_promotion_requests;

create or replace function public.review_creator_promotion_request(
  p_request_id uuid,
  p_status text,
  p_commission_rate_bps integer default null
) returns setof public.creator_promotion_requests
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if auth.uid() is null or p_status not in ('approved','rejected') then
    raise exception 'Invalid promotion review' using errcode = '42501';
  end if;
  if p_status = 'approved' and (p_commission_rate_bps is null or p_commission_rate_bps not between 1 and 10000) then
    raise exception 'A valid vendor commission rate is required for approval' using errcode = '22023';
  end if;
  if p_status = 'rejected' and p_commission_rate_bps is not null then
    raise exception 'Commission rate must be omitted when rejecting' using errcode = '22023';
  end if;
  return query
    update public.creator_promotion_requests r
       set status = p_status,
           commission_rate_bps = case when p_status = 'approved' then p_commission_rate_bps else null end,
           updated_at = now()
     where r.id = p_request_id
       and r.owner_id = auth.uid()
       and r.status = 'pending'
    returning r.*;
  if not found then
    raise exception 'Pending promotion request not found for this vendor' using errcode = 'P0002';
  end if;
end;
$$;
revoke all on function public.review_creator_promotion_request(uuid,text,integer) from public, anon;
grant execute on function public.review_creator_promotion_request(uuid,text,integer) to authenticated;
