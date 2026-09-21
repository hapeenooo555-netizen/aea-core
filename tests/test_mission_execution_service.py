"""Focused unit tests for MissionExecutionService PostgREST column and fallback behavior.

These tests verify the uncommitted changes in mission_execution_service.py
without requiring a live Supabase backend. They use lightweight fakes to
record the PostgREST query shape and simulate RPC failures.

Specifically validated:
  - get_execution queries by execution_id column (not DB PK id)
  - update_execution_state queries by execution_id column
  - claim_step falls back to PostgREST when claim_mission_step RPC fails
  - stale in-progress lease is recovered (marked failed, new step inserted)
  - load_steps_for_execution does NOT send owner_id filter (no such column)
  - update_step does NOT send owner_id filter (no such column on mission_steps)
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))


# ---------------------------------------------------------------------------
# Fake Supabase client — records query shape, returns configurable data
# ---------------------------------------------------------------------------

class FakeResponse:
    def __init__(self, data: Any = None):
        self.data = data


class FakeTable:
    def __init__(self, fake_client: "FakeClient", table_name: str):
        self._client = fake_client
        self._table = table_name
        self._filters: list[tuple[str, Any]] = []
        self._selected = False
        self._mode: str = "select"  # select | insert | update
        self._insert_data: dict | None = None
        self._update_data: dict | None = None

    def select(self, *_args, **kwargs) -> "FakeTable":
        self._selected = True
        self._mode = "select"
        return self

    def insert(self, data: dict[str, Any]) -> "FakeTable":
        self._mode = "insert"
        self._insert_data = dict(data)
        return self

    def update(self, data: dict[str, Any]) -> "FakeTable":
        self._mode = "update"
        self._update_data = dict(data)
        return self

    def eq(self, column: str, value: Any) -> "FakeTable":
        self._filters.append((column, value))
        return self

    def order(self, *_args, **kwargs) -> "FakeTable":
        return self

    def limit(self, n: int) -> "FakeTable":
        return self

    def execute(self) -> FakeResponse:
        return self._client._handle_table_op(self)


class FakeRPC:
    def __init__(self, fake_client: "FakeClient", func_name: str, params: dict):
        self._client = fake_client
        self._func_name = func_name
        self._params = params

    def execute(self) -> FakeResponse:
        return self._client._handle_rpc(self._func_name, self._params)


class FakeClient:
    def __init__(self):
        self.table_calls: list[tuple[str, str]] = []
        self.rpc_calls: list[tuple[str, dict]] = []
        self._last_filters: list[tuple[str, Any]] = []

        # Configurable response data
        self.execution_rows: list[dict] = []
        self.step_rows: list[dict] = []
        self.rpc_response: Any = []  # default: empty (RPC "fails")
        self.insert_responses: dict[str, list[dict]] = {}
        self.update_responses: dict[str, list[dict]] = {}
        self._current_table: str = ""

    def _snapshot_filters(self) -> list[tuple[str, Any]]:
        return list(self._last_filters)

    def table(self, name: str) -> FakeTable:
        self._current_table = name
        self.table_calls.append((name, name))
        return FakeTable(self, name)

    def rpc(self, func_name: str, params: dict) -> FakeRPC:
        self.rpc_calls.append((func_name, dict(params)))
        return FakeRPC(self, func_name, params)

    def _matches_filters(self, row: dict, filters: list[tuple[str, Any]]) -> bool:
        for col, val in filters:
            if col == "id":
                if row.get("id") != val:
                    return False
            elif row.get(col) != val:
                return False
        return True

    def _handle_table_op(self, table: FakeTable) -> FakeResponse:
        self._last_filters = table._filters
        table_name = table._table

        if table._mode == "select":
            source = self.execution_rows if table_name == "mission_executions" else self.step_rows
            data = [row for row in source if self._matches_filters(row, table._filters)]
            return FakeResponse(data)

        elif table._mode == "insert":
            inserted = dict(table._insert_data)
            if table_name == "mission_steps":
                inserted.setdefault("id", str(uuid4()))
                inserted.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                self.step_rows.append(inserted)
            elif table_name == "mission_executions":
                inserted.setdefault("id", str(uuid4()))
                inserted.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                self.execution_rows.append(inserted)
            return FakeResponse([inserted])

        elif table._mode == "update":
            updated = dict(table._update_data)
            source = self.step_rows if table_name == "mission_steps" else self.execution_rows
            matched = [row for row in source if self._matches_filters(row, table._filters)]
            for row in matched:
                row.update(updated)
            return FakeResponse(matched if matched else [])

        return FakeResponse([])

    def _handle_rpc(self, func_name: str, params: dict) -> FakeResponse:
        return FakeResponse(self.rpc_response)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_client():
    return FakeClient()


@pytest.fixture
def svc(fake_client):
    from app.services.mission_execution_service import MissionExecutionService
    return MissionExecutionService(client=fake_client, durable_required=True)


_EXECUTION_ID = str(uuid4())
_OWNER_ID = str(uuid4())
_STEP_ID = str(uuid4())
_MISSION_ID = str(uuid4())


def _make_execution_row(**overrides) -> dict:
    base = {
        "id": str(uuid4()),
        "mission_id": _MISSION_ID,
        "execution_id": _EXECUTION_ID,
        "idempotency_key": f"exec-key:{_EXECUTION_ID}",
        "status": "PENDING",
        "current_step_index": 0,
        "retry_count": 0,
        "result": {},
        "owner_id": _OWNER_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "started_at": None,
        "completed_at": None,
    }
    base.update(overrides)
    return base


def _make_step_row(**overrides) -> dict:
    base = {
        "id": _STEP_ID,
        "mission_id": _MISSION_ID,
        "execution_id": _EXECUTION_ID,
        "step_name": "test_step",
        "worker_role": "employee",
        "status": "in_progress",
        "attempt_index": 0,
        "idempotency_key": "step-key-1",
        "operation_key": "op-key-1",
        "claim_token": "token-1",
        "lease_expires_at": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat(),
        "started_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None,
        "result": {},
        "retry_category": None,
        "owner_id": _OWNER_ID,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# 1. get_execution uses execution_id column
# ---------------------------------------------------------------------------

def test_get_execution_queries_by_execution_id_column(svc, fake_client):
    """get_execution must filter on the execution_id column, not the DB PK id."""
    fake_client.execution_rows = [_make_execution_row()]

    svc.get_execution(_EXECUTION_ID, owner_id=_OWNER_ID)

    assert fake_client.table_calls[-1][0] == "mission_executions"
    filters = fake_client._last_filters
    col_names = [c for c, _ in filters]
    assert "execution_id" in col_names, (
        f"Expected .eq('execution_id', ...) in query filters, got: {filters}"
    )
    assert "id" not in col_names, (
        f"Unexpected .eq('id', ...) in query filters: {filters}"
    )
    assert ("owner_id", _OWNER_ID) in filters


def test_get_execution_returns_none_when_no_rows(svc, fake_client):
    """get_execution returns None (not error) when no rows match."""
    fake_client.execution_rows = []

    result = svc.get_execution(_EXECUTION_ID, owner_id=_OWNER_ID)

    assert result is None


# ---------------------------------------------------------------------------
# 2. update_execution_state uses execution_id column
# ---------------------------------------------------------------------------

def test_update_execution_state_queries_by_execution_id_column(svc, fake_client):
    """update_execution_state must target execution_id column, not id PK."""
    fake_client.execution_rows = [_make_execution_row()]

    svc.update_execution_state(
        _EXECUTION_ID, _OWNER_ID, status="RUNNING"
    )

    assert fake_client.table_calls[-1][0] == "mission_executions"
    filters = fake_client._last_filters
    col_names = [c for c, _ in filters]
    assert "execution_id" in col_names, (
        f"Expected .eq('execution_id', ...) in update filters, got: {filters}"
    )
    assert "id" not in col_names, (
        f"Unexpected .eq('id', ...) in update filters: {filters}"
    )
    assert ("owner_id", _OWNER_ID) in filters


# ---------------------------------------------------------------------------
# 3. claim_step PostgREST fallback
# ---------------------------------------------------------------------------

def test_claim_step_falls_back_to_postgREST_when_rpc_returns_empty(svc, fake_client):
    """When claim_mission_step RPC returns empty, fallback must use PostgREST."""
    fake_client.rpc_response = []  # RPC returns no data → fallback
    fake_client.execution_rows = [_make_execution_row()]

    result = svc.claim_step(
        execution_id=_EXECUTION_ID,
        owner_id=_OWNER_ID,
        step_id=_STEP_ID,
        attempt_index=0,
        idempotency_key="test-idempotency",
        step_name="test_step",
        operation_key="test-operation",
        claim_token="token-123",
    )

    assert result["success"] is True, f"Expected success, got: {result}"
    assert result.get("claimed") is True
    assert result["step"] is not None

    # RPC was attempted
    rpc_calls = [name for name, _ in fake_client.rpc_calls]
    assert "claim_mission_step" in rpc_calls, (
        f"Expected claim_mission_step RPC call, got: {rpc_calls}"
    )

    # PostgREST fallback: checked idempotency_key first
    assert fake_client.step_rows, "Expected a new step to be inserted"
    inserted = fake_client.step_rows[-1]
    assert inserted["idempotency_key"] == "test-idempotency"
    assert inserted["execution_id"] == _EXECUTION_ID
    assert inserted["operation_key"] == "test-operation"
    assert inserted["claim_token"] == "token-123"
    assert inserted["status"] == "in_progress"


def test_claim_step_fallback_uses_get_execution_for_mission_id(svc, fake_client):
    """The PostgREST fallback must retrieve the execution via get_execution to get mission_id."""
    fake_client.rpc_response = []
    fake_client.execution_rows = [_make_execution_row()]
    fake_client.step_rows = []

    svc.claim_step(
        execution_id=_EXECUTION_ID,
        owner_id=_OWNER_ID,
        step_id=_STEP_ID,
        attempt_index=0,
        idempotency_key="idempotent-step",
        step_name="test_step",
    )

    # Verify RPC was called with correct params
    rpc_name, rpc_params = fake_client.rpc_calls[-1]
    assert rpc_name == "claim_mission_step"
    assert rpc_params["p_execution_id"] == _EXECUTION_ID

    # Verify a step was inserted with the correct mission_id from the execution
    inserted = fake_client.step_rows[-1]
    assert inserted["mission_id"] == _MISSION_ID


# ---------------------------------------------------------------------------
# 4. Stale lease recovery
# ---------------------------------------------------------------------------

_STALE_TIME = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()


def test_claim_step_fallback_recovers_stale_in_progress_lease(svc, fake_client):
    """When a stale in_progress step exists, it should be marked failed and a new step claimed."""
    fake_client.rpc_response = []
    fake_client.execution_rows = [_make_execution_row()]
    existing_step = _make_step_row(
        status="in_progress",
        lease_expires_at=_STALE_TIME,
        idempotency_key="old-idempotency",
        operation_key="test-operation",
    )
    fake_client.step_rows = [existing_step]

    result = svc.claim_step(
        execution_id=_EXECUTION_ID,
        owner_id=_OWNER_ID,
        step_id=str(uuid4()),
        attempt_index=1,
        idempotency_key="new-idempotency",
        step_name="test_step",
        operation_key="test-operation",
    )

    assert result["success"] is True
    assert result.get("claimed") is True

    # The stale step should have been marked failed
    stale = fake_client.step_rows[0]
    assert stale["status"] == "failed", (
        f"Expected stale step marked failed, got: {stale['status']}"
    )
    assert stale["retry_category"] == "EXECUTION"


def test_claim_step_fallback_returns_existing_completed_step(svc, fake_client):
    """When a completed step with same operation_key exists, return it without inserting a new one."""
    fake_client.rpc_response = []
    fake_client.execution_rows = [_make_execution_row()]
    existing = _make_step_row(
        status="completed",
        idempotency_key="old-idempotency",
        operation_key="test-operation",
    )
    fake_client.step_rows = [existing]
    initial_count = len(fake_client.step_rows)

    result = svc.claim_step(
        execution_id=_EXECUTION_ID,
        owner_id=_OWNER_ID,
        step_id=str(uuid4()),
        attempt_index=0,
        idempotency_key="new-idempotency",
        step_name="test_step",
        operation_key="test-operation",
    )

    assert result["success"] is True
    assert result.get("claimed") is False
    assert result["step"]["status"] == "completed"
    assert len(fake_client.step_rows) == initial_count, (
        "Should not insert a new step when an existing completed step is found"
    )


def test_claim_step_fallback_returns_existing_by_idempotency_key(svc, fake_client):
    """When a step with same idempotency_key exists, return it without inserting."""
    fake_client.rpc_response = []
    fake_client.execution_rows = [_make_execution_row()]
    existing = _make_step_row(
        status="completed",
        idempotency_key="same-key",
    )
    fake_client.step_rows = [existing]
    initial_count = len(fake_client.step_rows)

    result = svc.claim_step(
        execution_id=_EXECUTION_ID,
        owner_id=_OWNER_ID,
        step_id=str(uuid4()),
        attempt_index=0,
        idempotency_key="same-key",
        step_name="test_step",
        operation_key="test-operation",
    )

    assert result["success"] is True
    assert result.get("claimed") is False
    assert result["step"]["idempotency_key"] == "same-key"
    assert len(fake_client.step_rows) == initial_count


# ---------------------------------------------------------------------------
# 5. load_steps_for_execution — no owner_id filter
# ---------------------------------------------------------------------------

def test_load_steps_for_execution_does_not_send_owner_id(svc, fake_client):
    """load_steps_for_execution must NOT filter mission_steps by owner_id."""
    fake_client.step_rows = [_make_step_row(), _make_step_row()]

    svc.load_steps_for_execution(_EXECUTION_ID, _OWNER_ID)

    assert fake_client.table_calls[-1][0] == "mission_steps"
    filters = fake_client._last_filters
    col_names = [c for c, _ in filters]
    assert "execution_id" in col_names, (
        f"Expected .eq('execution_id', ...) in load_steps query, got: {filters}"
    )
    assert "owner_id" not in col_names, (
        f"mission_steps has no owner_id column — should not filter by it: {filters}"
    )


# ---------------------------------------------------------------------------
# 6. update_step — no owner_id filter
# ---------------------------------------------------------------------------

def test_update_step_does_not_send_owner_id(svc, fake_client):
    """update_step must NOT filter mission_steps by owner_id (no such column)."""
    fake_client.step_rows = [_make_step_row()]

    svc.update_step(
        _EXECUTION_ID,
        _OWNER_ID,
        _STEP_ID,
        status="completed",
        claim_token="token-123",
    )

    assert fake_client.table_calls[-1][0] == "mission_steps"
    filters = fake_client._last_filters
    col_names = [c for c, _ in filters]

    # Must use id (step PK) and execution_id
    assert "id" in col_names, f"Expected .eq('id', step_id), got: {filters}"
    assert "execution_id" in col_names, f"Expected .eq('execution_id', ...), got: {filters}"

    # Must NOT use owner_id (column doesn't exist on mission_steps)
    assert "owner_id" not in col_names, (
        f"mission_steps has no owner_id column — should not filter: {filters}"
    )

    # Claim token filter must still be present (fencing)
    assert ("claim_token", "token-123") in filters


def test_update_step_fails_when_durable_required_and_no_rows(svc, fake_client):
    """When the step UPDATE returns no rows and durable_required=True, return failure."""
    fake_client.step_rows = []  # No rows to update

    result = svc.update_step(
        _EXECUTION_ID,
        _OWNER_ID,
        _STEP_ID,
        status="completed",
    )

    assert result["success"] is False
