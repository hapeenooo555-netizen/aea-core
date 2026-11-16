from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest


ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = [
    ROOT / "supabase/migrations/20261116160000_creator_commerce_foundation.sql",
    ROOT / "supabase/migrations/20261116190000_confirm_marketplace_payment.sql",
    ROOT / "supabase/migrations/20261116200000_marketplace_checkout_url.sql",
    ROOT / "supabase/migrations/20261116210000_marketplace_private_product_files.sql",
]


@pytest.fixture()
def pg():
    dsn = os.getenv("TEST_DATABASE_URL")
    if not dsn:
        pytest.skip("TEST_DATABASE_URL is not configured")

    with psycopg.connect(dsn, autocommit=True) as conn:
        # GitHub Actions supplies a fresh, isolated PostgreSQL service database.
        conn.execute("""
            do $$ begin
              if not exists (select 1 from pg_roles where rolname = 'anon') then
                create role anon nologin;
              end if;
              if not exists (select 1 from pg_roles where rolname = 'authenticated') then
                create role authenticated nologin;
              end if;
              if not exists (select 1 from pg_roles where rolname = 'service_role') then
                create role service_role nologin bypassrls;
              end if;
            end $$;
        """)
        conn.execute("drop schema if exists public cascade")
        conn.execute("create schema public")
        conn.execute("grant all on schema public to postgres")
        conn.execute("grant usage on schema public to anon, authenticated, service_role")
        conn.execute("create schema if not exists storage")
        conn.execute("grant usage on schema storage to authenticated, service_role")
        conn.execute("""
            create table storage.buckets (
              id text primary key, name text not null, public boolean not null default false,
              file_size_limit bigint, allowed_mime_types text[]
            )
        """)
        conn.execute("""
            create table storage.objects (
              id uuid primary key default gen_random_uuid(),
              bucket_id text not null references storage.buckets(id),
              name text not null,
              unique(bucket_id, name)
            )
        """)
        conn.execute("alter table storage.objects enable row level security")
        conn.execute("grant select, insert, update, delete on storage.objects to authenticated, service_role")
        conn.execute("""
            create or replace function storage.foldername(name text) returns text[]
            language sql immutable
            as 'select case when position(''/'' in name) = 0 then array[]::text[]
                else string_to_array(regexp_replace(name, ''/[^/]*$'', ''''), ''/'') end'
        """)
        conn.execute("create table auth.users (id uuid primary key)")
        conn.execute("""
            create or replace function auth.uid() returns uuid
            language sql stable
            as $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$
        """)
        conn.execute("""
            create table public.creator_products (
              id uuid primary key,
              owner_id uuid not null references auth.users(id),
              status text not null,
              currency text not null,
              price_amount bigint not null,
              title text not null,
              description text not null,
              product_type text not null
            )
        """)
        conn.execute("grant select on public.creator_products to authenticated, service_role")
        for migration in MIGRATIONS:
            conn.execute(migration.read_text())

        seller, buyer, outsider = uuid4(), uuid4(), uuid4()
        product = uuid4()
        conn.execute(
            "insert into auth.users(id) values (%s), (%s), (%s)",
            (seller, buyer, outsider),
        )
        conn.execute(
            """insert into public.creator_products
               (id, owner_id, status, currency, price_amount, title, description, product_type)
               values (%s, %s, 'listed', 'TZS', 25000, 'Test digital product',
                       'A sufficiently long product description for integration testing.',
                       'ebook')""",
            (product, seller),
        )
        yield conn, seller, buyer, outsider, product

        conn.execute("drop schema if exists public cascade")
        conn.execute("drop schema if exists auth cascade")
        conn.execute("drop schema if exists storage cascade")


def _as_user(conn, user_id):
    conn.execute("reset role")
    conn.execute("select set_config('request.jwt.claim.sub', %s, false)", (str(user_id),))
    conn.execute("set role authenticated")


def test_postgres_migrations_enforce_rls_and_confirm_payment_once(pg):
    conn, seller, buyer, outsider, product = pg

    # Checkout RPC derives the buyer from auth.uid() and snapshots the product.
    _as_user(conn, buyer)
    checkout = conn.execute(
        "select public.create_marketplace_checkout(%s, %s)",
        (product, "pg-integration-key-0001"),
    ).fetchone()[0]
    order_id = checkout["order_id"]
    assert checkout["status"] == "pending"
    assert checkout["total_amount"] == 25000
    assert checkout["provider"] == "test"

    # Move the isolated test order into the provider-confirmation state.
    conn.execute("reset role")
    conn.execute(
        """update public.marketplace_orders
              set provider = 'clickpesa', expires_at = now() + interval '10 minutes'
            where id = %s""",
        (order_id,),
    )
    event_ref = "clickpesa-test-reference-0001"
    conn.execute(
        """insert into public.payment_events
           (order_id, provider, provider_event_id, event_type, verification_status,
            amount, currency, payload_sha256)
           values (%s, 'clickpesa', %s, 'PAYMENT RECEIVED', 'received',
                   25000, 'TZS', repeat('a', 64))""",
        (order_id, event_ref),
    )

    # Buyers and sellers can see the order; unrelated users cannot.
    _as_user(conn, buyer)
    assert conn.execute(
        "select count(*) from public.marketplace_orders where id = %s", (order_id,)
    ).fetchone()[0] == 1
    _as_user(conn, seller)
    assert conn.execute(
        "select count(*) from public.marketplace_orders where id = %s", (order_id,)
    ).fetchone()[0] == 1
    _as_user(conn, outsider)
    assert conn.execute(
        "select count(*) from public.marketplace_orders where id = %s", (order_id,)
    ).fetchone()[0] == 0

    # Authenticated users cannot read or write private payment event records.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select count(*) from public.payment_events").fetchone()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            """insert into public.payment_events
               (order_id, provider, provider_event_id, event_type, payload_sha256)
               values (%s, 'clickpesa', 'forged-event', 'PAYMENT RECEIVED', repeat('b', 64))""",
            (order_id,),
        )

    conn.execute("reset role")
    assert conn.execute(
        "select has_function_privilege('authenticated', "
        "'public.confirm_marketplace_payment(uuid,text,bigint,text)', 'EXECUTE')"
    ).fetchone()[0] is False
    assert conn.execute(
        "select has_function_privilege('service_role', "
        "'public.confirm_marketplace_payment(uuid,text,bigint,text)', 'EXECUTE')"
    ).fetchone()[0] is True

    # Trusted service role confirms once; retry is idempotent.
    conn.execute("set role service_role")
    first = conn.execute(
        "select public.confirm_marketplace_payment(%s, %s, 25000, 'TZS')",
        (order_id, event_ref),
    ).fetchone()[0]
    second = conn.execute(
        "select public.confirm_marketplace_payment(%s, %s, 25000, 'TZS')",
        (order_id, event_ref),
    ).fetchone()[0]
    assert first["confirmed"] is True
    assert first["already_processed"] is False
    assert second["already_processed"] is True

    conn.execute("reset role")
    assert conn.execute(
        "select count(*) from public.product_entitlements where order_id = %s",
        (order_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "select count(*) from public.seller_ledger_entries where order_id = %s",
        (order_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "select status from public.marketplace_orders where id = %s", (order_id,)
    ).fetchone()[0] == "paid"


def test_checkout_is_idempotent_under_concurrent_requests_and_bound_to_product(pg):
    from concurrent.futures import ThreadPoolExecutor

    conn, seller, buyer, outsider, product = pg
    other_product = uuid4()
    conn.execute(
        """insert into public.creator_products
           (id, owner_id, status, currency, price_amount, title, description, product_type)
           values (%s, %s, 'listed', 'TZS', 40000, 'Another digital product',
                   'Another sufficiently long description for integration testing.',
                   'guide')""",
        (other_product, seller),
    )
    dsn = os.environ["TEST_DATABASE_URL"]
    key = "pg-concurrent-checkout-key-0001"

    def create_checkout():
        with psycopg.connect(dsn, autocommit=True) as session:
            session.execute(
                "select set_config('request.jwt.claim.sub', %s, false)",
                (str(buyer),),
            )
            session.execute("set role authenticated")
            return session.execute(
                "select public.create_marketplace_checkout(%s, %s)",
                (product, key),
            ).fetchone()[0]

    # Two independent authenticated DB sessions race on the same buyer/key.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create_checkout(), range(2)))

    assert results[0]["order_id"] == results[1]["order_id"]
    assert sorted([results[0]["reused"], results[1]["reused"]]) == [False, True]
    assert conn.execute(
        "select count(*) from public.marketplace_orders where buyer_id = %s and idempotency_key = %s",
        (buyer, key),
    ).fetchone()[0] == 1
    assert conn.execute(
        "select count(*) from public.marketplace_order_items where order_id = %s",
        (results[0]["order_id"],),
    ).fetchone()[0] == 1

    # Reusing the same idempotency key for another product must fail closed.
    with psycopg.connect(dsn, autocommit=True) as session:
        session.execute(
            "select set_config('request.jwt.claim.sub', %s, false)",
            (str(buyer),),
        )
        session.execute("set role authenticated")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            session.execute(
                "select public.create_marketplace_checkout(%s, %s)",
                (other_product, key),
            )
, ''), '/') end
            $
        """)
        conn.execute("create schema if not exists auth")
        conn.execute("create table auth.users (id uuid primary key)")
        conn.execute("""
            create or replace function auth.uid() returns uuid
            language sql stable
            as $$ select nullif(current_setting('request.jwt.claim.sub', true), '')::uuid $$
        """)
        conn.execute("""
            create table public.creator_products (
              id uuid primary key,
              owner_id uuid not null references auth.users(id),
              status text not null,
              currency text not null,
              price_amount bigint not null,
              title text not null,
              description text not null,
              product_type text not null
            )
        """)
        conn.execute("grant select on public.creator_products to authenticated, service_role")
        for migration in MIGRATIONS:
            conn.execute(migration.read_text())

        seller, buyer, outsider = uuid4(), uuid4(), uuid4()
        product = uuid4()
        conn.execute(
            "insert into auth.users(id) values (%s), (%s), (%s)",
            (seller, buyer, outsider),
        )
        conn.execute(
            """insert into public.creator_products
               (id, owner_id, status, currency, price_amount, title, description, product_type)
               values (%s, %s, 'listed', 'TZS', 25000, 'Test digital product',
                       'A sufficiently long product description for integration testing.',
                       'ebook')""",
            (product, seller),
        )
        yield conn, seller, buyer, outsider, product

        conn.execute("drop schema if exists public cascade")
        conn.execute("drop schema if exists auth cascade")


def _as_user(conn, user_id):
    conn.execute("reset role")
    conn.execute("select set_config('request.jwt.claim.sub', %s, false)", (str(user_id),))
    conn.execute("set role authenticated")


def test_postgres_migrations_enforce_rls_and_confirm_payment_once(pg):
    conn, seller, buyer, outsider, product = pg

    # Checkout RPC derives the buyer from auth.uid() and snapshots the product.
    _as_user(conn, buyer)
    checkout = conn.execute(
        "select public.create_marketplace_checkout(%s, %s)",
        (product, "pg-integration-key-0001"),
    ).fetchone()[0]
    order_id = checkout["order_id"]
    assert checkout["status"] == "pending"
    assert checkout["total_amount"] == 25000
    assert checkout["provider"] == "test"

    # Move the isolated test order into the provider-confirmation state.
    conn.execute("reset role")
    conn.execute(
        """update public.marketplace_orders
              set provider = 'clickpesa', expires_at = now() + interval '10 minutes'
            where id = %s""",
        (order_id,),
    )
    event_ref = "clickpesa-test-reference-0001"
    conn.execute(
        """insert into public.payment_events
           (order_id, provider, provider_event_id, event_type, verification_status,
            amount, currency, payload_sha256)
           values (%s, 'clickpesa', %s, 'PAYMENT RECEIVED', 'received',
                   25000, 'TZS', repeat('a', 64))""",
        (order_id, event_ref),
    )

    # Buyers and sellers can see the order; unrelated users cannot.
    _as_user(conn, buyer)
    assert conn.execute(
        "select count(*) from public.marketplace_orders where id = %s", (order_id,)
    ).fetchone()[0] == 1
    _as_user(conn, seller)
    assert conn.execute(
        "select count(*) from public.marketplace_orders where id = %s", (order_id,)
    ).fetchone()[0] == 1
    _as_user(conn, outsider)
    assert conn.execute(
        "select count(*) from public.marketplace_orders where id = %s", (order_id,)
    ).fetchone()[0] == 0

    # Authenticated users cannot read or write private payment event records.
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute("select count(*) from public.payment_events").fetchone()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            """insert into public.payment_events
               (order_id, provider, provider_event_id, event_type, payload_sha256)
               values (%s, 'clickpesa', 'forged-event', 'PAYMENT RECEIVED', repeat('b', 64))""",
            (order_id,),
        )

    conn.execute("reset role")
    assert conn.execute(
        "select has_function_privilege('authenticated', "
        "'public.confirm_marketplace_payment(uuid,text,bigint,text)', 'EXECUTE')"
    ).fetchone()[0] is False
    assert conn.execute(
        "select has_function_privilege('service_role', "
        "'public.confirm_marketplace_payment(uuid,text,bigint,text)', 'EXECUTE')"
    ).fetchone()[0] is True

    # Trusted service role confirms once; retry is idempotent.
    conn.execute("set role service_role")
    first = conn.execute(
        "select public.confirm_marketplace_payment(%s, %s, 25000, 'TZS')",
        (order_id, event_ref),
    ).fetchone()[0]
    second = conn.execute(
        "select public.confirm_marketplace_payment(%s, %s, 25000, 'TZS')",
        (order_id, event_ref),
    ).fetchone()[0]
    assert first["confirmed"] is True
    assert first["already_processed"] is False
    assert second["already_processed"] is True

    conn.execute("reset role")
    assert conn.execute(
        "select count(*) from public.product_entitlements where order_id = %s",
        (order_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "select count(*) from public.seller_ledger_entries where order_id = %s",
        (order_id,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "select status from public.marketplace_orders where id = %s", (order_id,)
    ).fetchone()[0] == "paid"


def test_checkout_is_idempotent_under_concurrent_requests_and_bound_to_product(pg):
    from concurrent.futures import ThreadPoolExecutor

    conn, seller, buyer, outsider, product = pg
    other_product = uuid4()
    conn.execute(
        """insert into public.creator_products
           (id, owner_id, status, currency, price_amount, title, description, product_type)
           values (%s, %s, 'listed', 'TZS', 40000, 'Another digital product',
                   'Another sufficiently long description for integration testing.',
                   'guide')""",
        (other_product, seller),
    )
    dsn = os.environ["TEST_DATABASE_URL"]
    key = "pg-concurrent-checkout-key-0001"

    def create_checkout():
        with psycopg.connect(dsn, autocommit=True) as session:
            session.execute(
                "select set_config('request.jwt.claim.sub', %s, false)",
                (str(buyer),),
            )
            session.execute("set role authenticated")
            return session.execute(
                "select public.create_marketplace_checkout(%s, %s)",
                (product, key),
            ).fetchone()[0]

    # Two independent authenticated DB sessions race on the same buyer/key.
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: create_checkout(), range(2)))

    assert results[0]["order_id"] == results[1]["order_id"]
    assert sorted([results[0]["reused"], results[1]["reused"]]) == [False, True]
    assert conn.execute(
        "select count(*) from public.marketplace_orders where buyer_id = %s and idempotency_key = %s",
        (buyer, key),
    ).fetchone()[0] == 1
    assert conn.execute(
        "select count(*) from public.marketplace_order_items where order_id = %s",
        (results[0]["order_id"],),
    ).fetchone()[0] == 1

    # Reusing the same idempotency key for another product must fail closed.
    with psycopg.connect(dsn, autocommit=True) as session:
        session.execute(
            "select set_config('request.jwt.claim.sub', %s, false)",
            (str(buyer),),
        )
        session.execute("set role authenticated")
        with pytest.raises(psycopg.errors.InvalidParameterValue):
            session.execute(
                "select public.create_marketplace_checkout(%s, %s)",
                (other_product, key),
            )


def test_marketplace_file_metadata_and_storage_objects_are_owner_scoped(pg):
    conn, seller, buyer, outsider, product = pg
    path = f"{seller}/{product}/private-product.pdf"
    conn.execute(
        """insert into public.marketplace_product_files
           (product_id, owner_id, storage_path, content_type, file_size_bytes, file_name)
           values (%s, %s, %s, 'application/pdf', 1024, 'product.pdf')""",
        (product, seller, path),
    )
    conn.execute(
        "insert into storage.objects(bucket_id, name) values ('marketplace-products', %s)",
        (path,),
    )

    _as_user(conn, seller)
    assert conn.execute(
        "select count(*) from public.marketplace_product_files where product_id = %s",
        (product,),
    ).fetchone()[0] == 1
    assert conn.execute(
        "select count(*) from storage.objects where bucket_id = 'marketplace-products' and name = %s",
        (path,),
    ).fetchone()[0] == 1

    _as_user(conn, buyer)
    assert conn.execute(
        "select count(*) from public.marketplace_product_files where product_id = %s",
        (product,),
    ).fetchone()[0] == 0
    assert conn.execute(
        "select count(*) from storage.objects where bucket_id = 'marketplace-products' and name = %s",
        (path,),
    ).fetchone()[0] == 0
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        conn.execute(
            "insert into storage.objects(bucket_id, name) values ('marketplace-products', %s)",
            (path,),
        )
