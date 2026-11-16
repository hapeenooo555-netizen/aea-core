-- Private marketplace product files and owner-scoped Storage access.
begin;

create table if not exists public.marketplace_product_files (
    product_id uuid primary key references public.creator_products(id) on delete cascade,
    owner_id uuid not null references auth.users(id) on delete cascade,
    storage_path text not null unique,
    content_type text not null check (content_type in ('application/pdf','video/mp4','application/epub+zip')),
    file_size_bytes bigint not null check (file_size_bytes between 1 and 104857600),
    file_name text not null check (char_length(file_name) between 1 and 180),
    created_at timestamptz not null default now(),
    updated_at timestamptz not null default now(),
    constraint marketplace_product_files_path_owner check (
      split_part(storage_path, '/', 1) = owner_id::text
      and split_part(storage_path, '/', 2) = product_id::text
      and storage_path !~ '(^/|\\.\\.|//)'
    )
);

alter table public.marketplace_product_files enable row level security;
revoke all on public.marketplace_product_files from anon, authenticated;
grant select, insert, update on public.marketplace_product_files to authenticated;
grant all on public.marketplace_product_files to service_role;

drop policy if exists marketplace_product_files_owner_all on public.marketplace_product_files;
create policy marketplace_product_files_owner_all on public.marketplace_product_files
  for all to authenticated
  using (
    owner_id = (select auth.uid())
    and exists (
      select 1 from public.creator_products p
      where p.id = product_id and p.owner_id = (select auth.uid())
    )
  )
  with check (
    owner_id = (select auth.uid())
    and exists (
      select 1 from public.creator_products p
      where p.id = product_id and p.owner_id = (select auth.uid())
    )
  );

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'marketplace-products',
  'marketplace-products',
  false,
  104857600,
  array['application/pdf','video/mp4','application/epub+zip']
)
on conflict (id) do update set
  public = false,
  file_size_limit = 104857600,
  allowed_mime_types = array['application/pdf','video/mp4','application/epub+zip'];

drop policy if exists marketplace_product_files_owner_read on storage.objects;
create policy marketplace_product_files_owner_read on storage.objects
  for select to authenticated
  using (
    bucket_id = 'marketplace-products'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

drop policy if exists marketplace_product_files_owner_insert on storage.objects;
create policy marketplace_product_files_owner_insert on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'marketplace-products'
    and (storage.foldername(name))[1] = (select auth.uid())::text
    and exists (
      select 1 from public.creator_products p
      where p.id::text = (storage.foldername(name))[2]
        and p.owner_id = (select auth.uid())
    )
  );

drop policy if exists marketplace_product_files_owner_update on storage.objects;
create policy marketplace_product_files_owner_update on storage.objects
  for update to authenticated
  using (
    bucket_id = 'marketplace-products'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  )
  with check (
    bucket_id = 'marketplace-products'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

drop policy if exists marketplace_product_files_owner_delete on storage.objects;
create policy marketplace_product_files_owner_delete on storage.objects
  for delete to authenticated
  using (
    bucket_id = 'marketplace-products'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );

commit;
