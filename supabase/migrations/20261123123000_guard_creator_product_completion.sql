-- Enforce completion invariants inside PostgreSQL as well as at the API layer.
-- This prevents clients from bypassing FastAPI and directly setting listed/marketplace flags.
create or replace function public.guard_creator_product_completion()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  latest_asset public.content_asset_versions%rowtype;
  chapter_count integer := 0;
  valid_chapter_count integer := 0;
  core_text_length integer := 0;
begin
  if new.status = 'listed' or coalesce(new.marketplace_enabled, false) then
    if new.completion_status is distinct from 'finished' then
      raise exception 'Product must be finished before listing or Marketplace activation'
        using errcode = '23514';
    end if;

    select * into latest_asset
      from public.content_asset_versions
      where owner_id = new.owner_id and asset_id = new.asset_id
      order by version desc
      limit 1;

    if latest_asset.id is null or latest_asset.status <> 'approved' then
      raise exception 'Latest product content must be approved'
        using errcode = '23514';
    end if;

    core_text_length := case
      when jsonb_typeof(latest_asset.content->'core_content') = 'string'
        then char_length(trim(latest_asset.content->>'core_content'))
      else 0
    end;

    if core_text_length < 300 then
      if jsonb_typeof(latest_asset.content->'chapters') = 'array' then
        select count(*),
               count(*) filter (
                 where jsonb_typeof(chapter) = 'object'
                   and char_length(trim(coalesce(chapter->>'body', chapter->>'content', chapter->>'text', ''))) >= 200
               )
          into chapter_count, valid_chapter_count
          from jsonb_array_elements(latest_asset.content->'chapters') as chapters(chapter);
      elsif jsonb_typeof(latest_asset.content->'chapter_content') = 'array' then
        select count(*),
               count(*) filter (
                 where jsonb_typeof(chapter) = 'object'
                   and char_length(trim(coalesce(chapter->>'body', chapter->>'content', chapter->>'text', ''))) >= 200
               )
          into chapter_count, valid_chapter_count
          from jsonb_array_elements(latest_asset.content->'chapter_content') as chapters(chapter);
      end if;

      if chapter_count = 0 or valid_chapter_count <> chapter_count then
        raise exception 'Product content must contain substantive readable text or complete chapters'
          using errcode = '23514';
      end if;
    end if;
  end if;
  return new;
end;
$$;

drop trigger if exists creator_products_completion_guard on public.creator_products;
create trigger creator_products_completion_guard
before insert or update
on public.creator_products
for each row execute function public.guard_creator_product_completion();
