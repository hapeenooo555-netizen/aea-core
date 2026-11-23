-- Private, owner-scoped digital product files. No public bucket URLs.
insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values (
  'aea-product-files',
  'aea-product-files',
  false,
  52428800,
  array['application/pdf','application/epub+zip','application/zip','application/vnd.openxmlformats-officedocument.wordprocessingml.document']
)
on conflict (id) do update set
  public = false,
  file_size_limit = excluded.file_size_limit,
  allowed_mime_types = excluded.allowed_mime_types;

create table if not exists public.creator_product_files (
  id uuid primary key default gen_random_uuid(),
  product_id uuid not null references public.creator_products(id) on delete cascade,
  owner_id uuid not null references auth.users(id) on delete cascade,
  bucket_id text not null default 'aea-product-files' check (bucket_id = 'aea-product-files'),
  object_path text not null unique,
  original_filename text not null check (char_length(original_filename) between 1 and 255),
  mime_type text not null check (mime_type in ('application/pdf','application/epub+zip','application/zip','application/vnd.openxmlformats-officedocument.wordprocessingml.document')),
  size_bytes bigint not null check (size_bytes > 0 and size_bytes <= 52428800),
  status text not null default 'pending' check (status in ('pending','ready','rejected')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);
create index if not exists idx_creator_product_files_owner_product
  on public.creator_product_files(owner_id, product_id, created_at desc);
alter table public.creator_product_files enable row level security;
drop policy if exists "owners read own product files" on public.creator_product_files;
create policy "owners read own product files" on public.creator_product_files
  for select to authenticated using ((select auth.uid()) = owner_id);
drop policy if exists "owners create own product files" on public.creator_product_files;
create policy "owners create own product files" on public.creator_product_files
  for insert to authenticated with check (
    (select auth.uid()) = owner_id
    and exists (select 1 from public.creator_products p where p.id = product_id and p.owner_id = (select auth.uid()))
  );
drop policy if exists "owners update own product files" on public.creator_product_files;
create policy "owners update own product files" on public.creator_product_files
  for update to authenticated using ((select auth.uid()) = owner_id)
  with check ((select auth.uid()) = owner_id);
revoke all on public.creator_product_files from anon, authenticated;
grant select, insert, update on public.creator_product_files to authenticated;
grant all on public.creator_product_files to service_role;

drop policy if exists "product file owner uploads" on storage.objects;
create policy "product file owner uploads" on storage.objects
  for insert to authenticated
  with check (
    bucket_id = 'aea-product-files'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );
drop policy if exists "product file owner reads" on storage.objects;
create policy "product file owner reads" on storage.objects
  for select to authenticated
  using (
    bucket_id = 'aea-product-files'
    and (storage.foldername(name))[1] = (select auth.uid())::text
  );
