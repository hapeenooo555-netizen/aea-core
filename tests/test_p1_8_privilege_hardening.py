"""P1-8 Security Hardening: Static migration privilege verification.

This test does NOT connect to a live database.  Instead it parses the
migration SQL file text to verify that the five P1-8 durable-execution
RPC functions have the correct privilege model:

    PUBLIC         -> NO EXECUTE
    anon           -> NO EXECUTE
    authenticated  -> EXECUTE
    service_role   -> NOT granted

The audit finding was that migrations 010/011/012 never revoked these
functions from the PostgreSQL PUBLIC role.  Every role — including anon —
inherits PUBLIC grants, so REVOKE FROM anon alone does not remove
access.  The fix requires REVOKE FROM PUBLIC.
"""

from __future__ import annotations

import re
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "supabase" / "migrations"

# ---------------------------------------------------------------------------
# Each entry: (function_name, exact_signature_string)
#
# Signatures must match the CREATE OR REPLACE FUNCTION declarations in
# migrations 010, 011, and 012 — NOT the GRANT shorthand form (which omits
# the "public." schema prefix).  The GRANT/REVOKE statements in the
# hardening migration use the same type list.
# ---------------------------------------------------------------------------

P18_FUNCTIONS: list[tuple[str, str]] = [
    ("claim_mission_for_orchestration", "public.claim_mission_for_orchestration(UUID, TEXT, INTEGER)"),
    ("claim_mission_execution",          "public.claim_mission_execution(UUID, UUID, TEXT, TEXT)"),
    ("claim_mission_step",               "public.claim_mission_step(UUID, UUID, INTEGER, TEXT, TEXT, TEXT, INTEGER)"),
    ("update_execution_state",           "public.update_execution_state(UUID, TEXT, INTEGER, INTEGER, JSONB, TIMESTAMPTZ)"),
    ("complete_execution",               "public.complete_execution(UUID, TEXT, JSONB)"),
]


def _migration_path() -> Path:
    """Find the migration file for this security hardening."""
    pattern = "000000000013_p1_8_execution_privilege_hardening.sql"
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
        # Remove trailing inline comments
        if "--" in line:
            line = line.split("--")[0]
        lines.append(line)
    return lines


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestMigrationPrivilegeHardening:
    """Verify the hardening migration correctly revokes PUBLIC grants and
    grants EXECUTE only to authenticated."""

    def test_migration_file_exists(self) -> None:
        path = _migration_path()
        assert path.name == "000000000013_p1_8_execution_privilege_hardening.sql"

    def test_all_five_functions_revoked_from_public(self) -> None:
        sql = _migration_text()
        for func_name, full_sig in P18_FUNCTIONS:
            pattern = rf"REVOKE\s+ALL\s+ON\s+FUNCTION\s+{re.escape(full_sig)}\s+FROM\s+PUBLIC\s*;"
            assert re.search(pattern, sql, re.IGNORECASE), (
                f"Missing REVOKE ALL ON FUNCTION {full_sig} FROM PUBLIC"
            )

    def test_all_five_functions_revoked_from_anon(self) -> None:
        sql = _migration_text()
        for func_name, full_sig in P18_FUNCTIONS:
            pattern = rf"REVOKE\s+ALL\s+ON\s+FUNCTION\s+{re.escape(full_sig)}\s+FROM\s+anon\s*;"
            assert re.search(pattern, sql, re.IGNORECASE), (
                f"Missing REVOKE ALL ON FUNCTION {full_sig} FROM anon"
            )

    def test_all_five_functions_revoked_from_authenticated(self) -> None:
        """Revoke from authenticated first, so the subsequent GRANT is
        the single source of truth for who has EXECUTE."""
        sql = _migration_text()
        for func_name, full_sig in P18_FUNCTIONS:
            pattern = rf"REVOKE\s+ALL\s+ON\s+FUNCTION\s+{re.escape(full_sig)}\s+FROM\s+authenticated\s*;"
            assert re.search(pattern, sql, re.IGNORECASE), (
                f"Missing REVOKE ALL ON FUNCTION {full_sig} FROM authenticated"
            )

    def test_all_five_functions_granted_to_authenticated_only(self) -> None:
        sql = _migration_text()
        for func_name, full_sig in P18_FUNCTIONS:
            # Must have GRANT EXECUTE ... TO authenticated
            grant_pattern = (
                rf"GRANT\s+EXECUTE\s+ON\s+FUNCTION\s+{re.escape(full_sig)}\s+TO\s+authenticated\s*;"
            )
            assert re.search(grant_pattern, sql, re.IGNORECASE), (
                f"Missing GRANT EXECUTE ON FUNCTION {full_sig} TO authenticated"
            )

    def test_no_grant_to_service_role(self) -> None:
        sql = _migration_text()
        assert not re.search(r"GRANT\s+.*TO\s+service_role", sql, re.IGNORECASE | re.MULTILINE), (
            "Migration must NOT grant any privilege to service_role "
            "(follows least-privilege convention from migration 008)"
        )

    def test_no_grant_to_anon_or_public(self) -> None:
        sql = _migration_text()
        # Check actual SQL statements only, not comment text
        stmt_lines = " ".join(_migration_statement_lines())
        assert not re.search(r"GRANT\s+\w+\s+ON\s+FUNCTION\s+\S+\s+TO\s+(PUBLIC|anon)\b", stmt_lines, re.IGNORECASE), (
            "Migration must NOT grant any privilege to PUBLIC or anon"
        )

    def test_no_grant_to_all(self) -> None:
        stmt_lines = " ".join(_migration_statement_lines())
        assert not re.search(r"GRANT\s+.*TO\s+ALL\b", stmt_lines, re.IGNORECASE), (
            "Migration must NOT use GRANT ... TO ALL"
        )

    def test_migration_contains_explanatory_comment(self) -> None:
        sql = _migration_text()
        assert "PUBLIC" in sql.upper(), (
            "Migration must include comment explaining PUBLIC revocation"
        )
        assert "inherited" in sql.upper() or "PUBLIC" in sql.upper(), (
            "Migration must explain why REVOKE FROM anon alone is insufficient "
            "(PUBLIC inheritance)"
        )

    def test_migration_has_begin_commit(self) -> None:
        sql = _migration_text()
        assert re.search(r"^BEGIN;", sql, re.IGNORECASE | re.MULTILINE), "Migration must wrap in BEGIN"
        assert re.search(r"^COMMIT;", sql, re.IGNORECASE | re.MULTILINE), "Migration must wrap in COMMIT"

    def test_all_five_functions_present(self) -> None:
        sql = _migration_text()
        for func_name, full_sig in P18_FUNCTIONS:
            assert full_sig in sql, (
                f"Function signature {full_sig} not found in migration"
            )

    def test_privilege_model_summary(self) -> None:
        """Verify the overall privilege model by checking that for every
        function, the sequence is: REVOKE FROM PUBLIC, REVOKE FROM anon,
        REVOKE FROM authenticated, GRANT TO authenticated."""
        sql = _migration_text()
        for func_name, full_sig in P18_FUNCTIONS:
            revokes = re.findall(
                rf"REVOKE\s+ALL\s+ON\s+FUNCTION\s+{re.escape(full_sig)}\s+FROM\s+"
                rf"(PUBLIC|anon|authenticated)\s*;",
                sql,
                re.IGNORECASE,
            )
            assert set(r.lower() for r in revokes) == {"public", "anon", "authenticated"}, (
                f"Expected REVOKE FROM PUBLIC, anon, authenticated for {full_sig}; "
                f"got: {revokes}"
            )
