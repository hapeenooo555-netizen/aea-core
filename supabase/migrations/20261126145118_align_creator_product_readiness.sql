-- Use the same saved chapter formats as the Core finish endpoint.
create or replace function public.guard_creator_product_completion()
returns trigger
language plpgsql
security invoker
set search_path = public
as $$
declare
  saved_content jsonb;
  saved_status text;
  core jsonb;
  chapters jsonb;
  chapter_count integer;
  valid_chapter_count integer;
begin
  if new.status is distinct from 'listed' and not coalesce(new.marketplace_enabled, false) then
    return new;
  end if;
  if new.completion_status is distinct from 'finished' then
    raise exception 'Product must be finished before listing or Marketplace activation'
      using errcode = '23514';
  end if;

  if exists (
    select 1 from public.creator_product_files f
    where f.product_id = new.id and f.owner_id = new.owner_id
      and f.bucket_id = 'aea-product-files' and f.status = 'ready'
  ) then
    return new;
  end if;

  select v.content, v.status into saved_content, saved_status
    from public.content_asset_versions v
    where v.owner_id = new.owner_id and v.asset_id = new.asset_id
    order by v.version desc
    limit 1;
  if saved_status is distinct from 'approved' then
    raise exception 'Latest product content must be approved or a verified private product file must exist'
      using errcode = '23514';
  end if;

  core := saved_content->'core_content';
  if jsonb_typeof(core) = 'string' and char_length(regexp_replace(core #>> '{}', '^\s+|\s+$', '', 'g')) >= 300 then
    return new;
  end if;

  select candidate into chapters
  from unnest(array[
    saved_content->'chapters', saved_content->'chapter_content', saved_content->'modules',
    core->'chapters', core->'chapter_content', core->'modules'
  ]) with ordinality as candidates(candidate, priority)
  where case when jsonb_typeof(candidate) = 'array' then jsonb_array_length(candidate) > 0 else false end
  order by priority limit 1;

  select count(*), count(*) filter (where char_length(body) >= 200)
  into chapter_count, valid_chapter_count
  from jsonb_array_elements(chapters) as items(chapter)
  cross join lateral (
    select regexp_replace(raw #>> '{}', '^\s+|\s+$', '', 'g') as body
    from unnest(array[chapter->'body', chapter->'content', chapter->'text',
                      chapter->'chapter_content', chapter]) with ordinality as texts(raw, priority)
    where jsonb_typeof(raw) = 'string'
      and regexp_replace(raw #>> '{}', '^\s+|\s+$', '', 'g') <> ''
    order by priority limit 1
  ) as chapter_text;

  if chapter_count = 0 or valid_chapter_count <> jsonb_array_length(chapters) then
    raise exception 'Product content must contain substantive readable text or complete chapters'
      using errcode = '23514';
  end if;
  return new;
end;
$$;

-- Preserve archived records and stop duplicate active products at the database.
-- If duplicates exist when deployed, the index build fails without deleting rows.
create unique index if not exists creator_products_one_active_per_asset
on public.creator_products (owner_id, asset_id)
where status in ('draft', 'listed');
