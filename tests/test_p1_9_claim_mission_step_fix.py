"""P1-9-FIX: Static verification of the claim_mission_step lookup correction.

This test parses the migration SQL file text to verify that the corrected
claim_mission_step function:

1. Has the exact 7-argument signature deployed by migration 011
2. Uses the corrected ``WHERE execution_id = p_execution_id`` lookup
3. Does NOT use the incorrect ``WHERE id = p_execution_id`` for the
   execution lookup
4. Remains SECURITY INVOKER (not SECURITY DEFINER)
5. Does NOT alter table/RLS privileges (CREATE OR REPLACE FUNCTION
   preserves existing grants; migration 013's privilege hardening is
   left untouched)
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "supabase" / "migrations"

EXPECTED_SIGNATURE = (
    "public.claim_mission_step(UUID, UUID, INTEGER, TEXT, TEXT, TEXT, INTEGER)"
)

EXPECTED_PARAMS = [
    "p_execution_id UUID",
    "p_step_id UUID",
    "p_attempt_index INTEGER",
    "p_idempotency_key TEXT",
    "p_operation_key TEXT",
    "p_claim_token TEXT",
    "p_lease_seconds INTEGER DEFAULT 60",
]


def _migration_path() -> Path:
    pattern = "000000000014_fix_claim_mission_step_lookup.sql"
    path = MIGRATIONS_DIR / pattern
    assert path.exists(), f"Migration file not found: {path}"
    return path


def _migration_text() -> str:
    return _migration_path().read_text()


def _migration_statement_lines() -> list[str]:
    """Return only actual SQL statement lines (strip SQL comments)."""
    text = _migration_text()
    lines = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("--"):
            continue
        if "--" in line:
            line = line.split("--")[0]
        lines.append(line)
    return lines


class TestClaimMissionStepFix:
    """Verify the corrected claim_mission_step function."""

    def test_migration_file_exists(self) -> None:
        path = _migration_path()
        assert path.name == "000000000014_fix_claim_mission_step_lookup.sql"

    def test_function_has_exact_7_argument_signature(self) -> None:
        sql = _migration_text()
        # The CREATE OR REPLACE FUNCTION must declare all 7 parameters.
        for param in EXPECTED_PARAMS:
            assert param in sql, f"Missing parameter declaration: {param}"

    def test_function_signature_matches_migration_011(self) -> None:
        normalised = re.sub(r"\s+", " ", _migration_text())
        # The CREATE OR REPLACE FUNCTION must target claim_mission_step
        # with all 7 parameters (names + types) matching migration 011.
        assert re.search(
            r"CREATE\s+OR\s+REPLACE\s+FUNCTION\s+"
            r"public\.claim_mission_step\s*\(\s*"
            r"p_execution_id\s+UUID\s*,\s*"
            r"p_step_id\s+UUID\s*,\s*"
            r"p_attempt_index\s+INTEGER\s*,\s*"
            r"p_idempotency_key\s+TEXT\s*,\s*"
            r"p_operation_key\s+TEXT\s*,\s*"
            r"p_claim_token\s+TEXT\s*,\s*"
            r"p_lease_seconds\s+INTEGER\s+DEFAULT\s+60\s*"
            r"\)\s+RETURNS\s+TABLE",
            normalised,
            re.IGNORECASE,
        ), "Function signature with 7 named parameters must match migration 011"

    def test_uses_corrected_execution_id_lookup(self) -> None:
        """The execution lookup must use execution_id, not id."""
        sql = _migration_text()
        # The first SELECT in the function body must query execution_id.
        pattern = (
            r"SELECT\s+mission_id\s+INTO\s+v_mission_id\s*"
            r"FROM\s+public\.mission_executions\s*"
            r"WHERE\s+execution_id\s*=\s*p_execution_id"
        )
        assert re.search(pattern, sql, re.IGNORECASE), (
            "Execution lookup must use 'WHERE execution_id = p_execution_id'"
        )

    def test_does_not_use_incorrect_id_lookup(self) -> None:
        """The execution lookup must NOT use 'WHERE id = p_execution_id'."""
        stmt_lines = " ".join(_migration_statement_lines())
        # Search for the specific bug pattern: a SELECT from mission_executions
        # filtering by id (not execution_id).
        pattern = (
            r"SELECT\s+mission_id\s+INTO\s+v_mission_id\s*"
            r"FROM\s+public\.mission_executions\s*"
            r"WHERE\s+id\s*=\s*p_execution_id"
        )
        assert not re.search(pattern, stmt_lines, re.IGNORECASE), (
            "Must NOT use 'WHERE id = p_execution_id' for execution lookup"
        )

    def test_does_not_alter_table_privileges(self) -> None:
        """The migration must NOT contain GRANT or REVOKE statements."""
        stmt_lines = " ".join(_migration_statement_lines())
        assert not re.search(r"\bGRANT\b", stmt_lines, re.IGNORECASE), (
            "Migration must NOT contain GRANT (privileges are preserved by "
            "CREATE OR REPLACE FUNCTION; migration 013 already set them)"
        )
        assert not re.search(r"\bREVOKE\b", stmt_lines, re.IGNORECASE), (
            "Migration must NOT contain REVOKE (migration 013 already "
            "established the privilege model)"
        )

    def test_preserves_security_invoker(self) -> None:
        sql = _migration_text()
        assert re.search(r"SECURITY\s+INVOKER", sql, re.IGNORECASE), (
            "Function must remain SECURITY INVOKER"
        )

    def test_does_not_use_security_definer(self) -> None:
        stmt_lines = " ".join(_migration_statement_lines())
        assert not re.search(r"SECURITY\s+DEFINER", stmt_lines, re.IGNORECASE), (
            "Migration must NOT use SECURITY DEFINER"
        )

    def test_preserves_all_other_function_logic(self) -> None:
        """Verify key logic blocks from migration 011 are preserved."""
        sql = _migration_text()
        # Idempotency check
        assert re.search(
            r"SELECT\s+\*\s+INTO\s+v_existing\s+FROM\s+public\.mission_steps\s+"
            r"WHERE\s+execution_id\s*=\s*p_execution_id\s+"
            r"AND\s+idempotency_key\s*=\s*p_idempotency_key",
            sql, re.IGNORECASE,
        ), "Idempotency check (v_existing) must be preserved"

        # Latest attempt lookup with FOR UPDATE
        assert re.search(
            r"SELECT\s+\*\s+INTO\s+v_latest\s+FROM\s+public\.mission_steps\s+"
            r"WHERE\s+execution_id\s*=\s*p_execution_id\s+"
            r"AND\s+operation_key\s*=\s*v_operation_key",
            sql, re.IGNORECASE,
        ), "Latest attempt lookup (v_latest) must be preserved"

        # Stale claim recovery
        assert re.search(
            r"SET\s+status\s*=\s*'failed'\s*,\s*"
            r"retry_category\s*=\s*'EXECUTION'\s*,\s*"
            r"claim_token\s*=\s*NULL\s*,\s*"
            r"result\s*=\s*jsonb_build_object\('recovered',\s*'stale_claim'\)",
            sql, re.IGNORECASE,
        ), "Stale claim recovery logic must be preserved"

        # Step insertion
        assert re.search(
            r"INSERT\s+INTO\s+public\.mission_steps",
            sql, re.IGNORECASE,
        ), "Step insertion must be preserved"

        # Unique violation handler
        assert re.search(
            r"WHEN\s+unique_violation\s+THEN",
            sql, re.IGNORECASE,
        ), "Unique violation handler must be preserved"

        # Return type
        assert re.search(
            r"RETURNS\s+TABLE\s*\(\s*step\s+JSONB,\s*claimed\s+BOOLEAN\s*\)",
            sql, re.IGNORECASE,
        ), "Return type must be preserved"

    def test_migration_has_begin_commit(self) -> None:
        sql = _migration_text()
        assert re.search(r"^BEGIN;", sql, re.IGNORECASE | re.MULTILINE), (
            "Migration must wrap in BEGIN"
        )
        assert re.search(r"^COMMIT;", sql, re.IGNORECASE | re.MULTILINE), (
            "Migration must wrap in COMMIT"
        )

    def test_migration_013_privilege_hardening_is_untouched(self) -> None:
        """Verify migration 013 still has the correct privilege model
        that this migration must preserve."""
        path = MIGRATIONS_DIR / "000000000013_p1_8_execution_privilege_hardening.sql"
        assert path.exists(), "Migration 013 must still exist"
        sql = path.read_text()
        # 013 must have REVOKE FROM PUBLIC for claim_mission_step
        assert re.search(
            r"REVOKE\s+ALL\s+ON\s+FUNCTION\s+public\.claim_mission_step"
            r"\(UUID,\s*UUID,\s*INTEGER,\s*TEXT,\s*TEXT,\s*TEXT,\s*INTEGER\)\s+FROM\s+PUBLIC",
            sql, re.IGNORECASE,
        ), "Migration 013 must revoke from PUBLIC"
        # 013 must have GRANT TO authenticated only
        assert re.search(
            r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+public\.claim_mission_step"
            r"\(UUID,\s*UUID,\s*INTEGER,\s*TEXT,\s*TEXT,\s*TEXT,\s*INTEGER\)\s+TO\s+authenticated",
            sql, re.IGNORECASE,
        ), "Migration 013 must grant EXECUTE to authenticated"
        # 013 must NOT grant to anon or service_role
        assert not re.search(
            r"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+public\.claim_mission_step.*TO\s+(anon|service_role|PUBLIC)",
            sql, re.IGNORECASE | re.DOTALL,
        ), "Migration 013 must not grant to anon, service_role, or PUBLIC"
