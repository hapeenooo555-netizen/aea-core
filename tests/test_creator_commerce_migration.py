from pathlib import Path


def test_payment_confirmation_migration_is_service_role_only_and_atomic():
    migration = Path(__file__).resolve().parents[1] / "supabase/migrations/20261117100000_confirm_marketplace_payment.sql"
    sql = migration.read_text()
    assert "auth.role()" in sql
    assert "service_role" in sql
    assert "for update" in sql.lower()
    assert "v_order.total_amount <> p_amount" in sql
    assert "insert into public.product_entitlements" in sql
    assert "on conflict (order_item_id) do nothing" in sql
    assert "on conflict (idempotency_key) do nothing" in sql
    assert "verification_status = 'verified'" in sql
    assert "status = 'paid'" in sql
    assert "from public.payment_events" in sql
