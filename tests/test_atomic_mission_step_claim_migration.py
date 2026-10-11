from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_atomic_claim_migration_serializes_missing_row_races():
    sql = (ROOT / "supabase/migrations/000000000017_atomic_mission_step_claim.sql").read_text()
    normalized = " ".join(sql.lower().split())

    assert "create or replace function public.claim_mission_step" in normalized
    assert "security invoker" in normalized
    assert "pg_advisory_xact_lock" in normalized
    assert "hashtextextended(p_execution_id::text || ':' || v_operation_key, 0)" in normalized
    assert normalized.index("pg_advisory_xact_lock") < normalized.index("select * into v_latest")
    assert "set status = 'failed'" in normalized
    assert "claim_token = null" in normalized
    assert "when unique_violation then" in normalized
    assert "drop table" not in normalized
    assert "delete from public.mission_steps" not in normalized


def test_database_migration_mirror_has_same_claim_safety_invariants():
    sql = (ROOT / "database/migrations/sprint7_2l_atomic_mission_step_claim.sql").read_text().lower()

    for invariant in (
        "create or replace function public.claim_mission_step",
        "security invoker",
        "pg_advisory_xact_lock",
        "hashtextextended(p_execution_id::text || ':' || v_operation_key, 0)",
        "claim_token = null",
        "when unique_violation then",
    ):
        assert invariant in sql
