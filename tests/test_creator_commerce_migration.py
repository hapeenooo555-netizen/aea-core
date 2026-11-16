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
