from pathlib import Path


def test_payment_confirmation_migration_is_service_role_only_and_atomic():
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase/migrations/20261116190000_confirm_marketplace_payment.sql"
    )
    sql = migration.read_text()
    lowered = sql.lower()

    assert "auth.role()" not in lowered
    assert "grant execute" in lowered and "to service_role" in lowered
    assert "revoke all" in lowered
    assert "for update" in lowered
    assert "p_provider_event_id text" in lowered
    assert "provider_payment_reference" in lowered
    assert "v_order.total_amount <> p_amount" in lowered
    assert "v_event.verification_status = 'rejected'" in lowered
    assert "insert into public.product_entitlements" in lowered
    assert "on conflict (order_item_id) do nothing" in lowered
    assert "on conflict (idempotency_key) do nothing" in lowered
    assert "verification_status = 'verified'" in lowered
    assert "status = 'paid'" in lowered
    assert "from public.payment_events" in lowered


def test_checkout_idempotency_is_race_safe_and_bound_to_same_product():
    migration = (
        Path(__file__).resolve().parents[1]
        / "supabase/migrations/20261116160000_creator_commerce_foundation.sql"
    )
    sql = migration.read_text().lower()

    assert "on conflict (buyer_id, idempotency_key) do nothing" in sql
    assert "if not found then" in sql
    assert "for update" in sql
    assert "oi.product_id = v_product.id" in sql
    assert "idempotency key already belongs to another product" in sql
    assert "unique (buyer_id, idempotency_key)" in sql


def test_marketplace_product_files_are_private_and_entitlement_delivery_is_gated():
    root = Path(__file__).resolve().parents[1]
    migration = (root / "supabase/migrations/20261116210000_marketplace_private_product_files.sql").read_text().lower()
    router = (root / "backend/app/routers/creator_commerce.py").read_text()

    assert "create table if not exists public.marketplace_product_files" in migration
    assert "enable row level security" in migration
    assert "public = false" in migration
    assert "104857600" in migration
    assert "storage.foldername(name)" in migration
    assert "marketplace_product_files_owner_insert" in migration
    assert "marketplace_product_files_owner_read" in migration

    assert '@router.put("/products/{product_id}/file")' in router
    assert '@router.get("/orders/{order_id}/download")' in router
    assert '.eq("buyer_id", buyer_id)' in router
    assert '.eq("status", "active")' in router
    assert 'create_signed_url(' in router
    assert 'files[0]["storage_path"], 60' in router
