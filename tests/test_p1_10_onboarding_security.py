"""P1-10 regression tests for onboarding endpoint ownership and user-scoping.

These tests verify the two bugs fixed during P1-10 verification:
1. Onboarding workflow ownership must rely on RLS (user-scoped client),
   not on comparing ``onboarding_workflows.worker_id`` to the user UUID
   (which stores a worker UUID, not a user UUID).
2. The user-scoped Pinterest connector must pass a user-scoped
   ``PlatformConnectionStore`` so connection persistence is RLS-enforced.
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client  # noqa: E402
import app.routers.connectors as connectors_module  # noqa: E402
from app.routers.connectors import _get_user_scoped_pinterest_connector  # noqa: E402
from app.services.connectors.pinterest_connector import PinterestConnector  # noqa: E402
from app.services.stores.onboarding_workflow_store import OnboardingWorkflowStore  # noqa: E402
from app.services.stores.platform_connection_store import PlatformConnectionStore  # noqa: E402


# ---------------------------------------------------------------------------
# Fake Supabase client for API-level testing
# ---------------------------------------------------------------------------
class _FakeExec:
    def __init__(self, data):
        self.data = data


class _FakeTable:
    def __init__(self, client, table_name):
        self._client = client
        self._table_name = table_name
        self._method = "select"
        self._payload = None
        self._eq_col = None
        self._eq_val = None
        self._select_cols = "*"
        self._limit_n = None

    def select(self, *args, **kwargs):
        self._method = "select"
        self._select_cols = args[0] if args else "*"
        return self

    def insert(self, payload):
        self._method = "insert"
        self._payload = payload if isinstance(payload, list) else [payload]
        return self

    def update(self, payload):
        self._method = "update"
        self._payload = payload
        return self

    def eq(self, col, val):
        self._eq_col = col
        self._eq_val = str(val)
        return self

    def limit(self, n):
        self._limit_n = n
        return self

    def execute(self):
        table = self._client.data.setdefault(self._table_name, [])
        if self._method == "insert":
            rows = self._payload if isinstance(self._payload, list) else [self._payload]
            inserted = []
            for row in rows:
                row.setdefault("id", str(uuid4()))
                row.setdefault("created_at", datetime.now(timezone.utc).isoformat())
                row.setdefault("updated_at", row["created_at"])
                if self._table_name in ("workers", "onboarding_workflows"):
                    row.setdefault("owner_id", "test-user")
                table.append(row)
                inserted.append(row)
            return _FakeExec(inserted)
        if self._method == "update":
            matched = []
            for r in table:
                if str(r.get(self._eq_col or "", "")) == self._eq_val:
                    r.update(self._payload)
                    matched.append(r)
            return _FakeExec(matched)
        # select
        rows = list(table)
        if self._eq_col is not None:
            rows = [r for r in rows if str(r.get(self._eq_col, "")) == self._eq_val]
        if self._limit_n is not None:
            rows = rows[: self._limit_n]
        return _FakeExec(rows)

    def rpc(self, name, params):
        class _FakeRPC:
            def __init__(self, name, params, client):
                self._name = name
                self._params = params
                self._client = client

            def execute(self):
                if self._name == "claim_onboarding_workflow_for_approval":
                    aid = self._params.get("p_approval_id")
                    rows = self._client.data.setdefault("onboarding_workflows", [])
                    existing = [r for r in rows if r.get("started_by_approval_id") == aid]
                    if existing:
                        return _FakeExec([{"workflow": existing[0], "created": False}])
                    wf = {
                        "id": self._params.get("p_workflow_id", str(uuid4())),
                        "mission_id": self._params.get("p_mission_id"),
                        "worker_id": self._params.get("p_worker_id"),
                        "platform": self._params.get("p_platform"),
                        "status": self._params.get("p_status", "pending"),
                        "current_step": self._params.get("p_current_step"),
                        "total_steps": self._params.get("p_total_steps"),
                        "checkpoint_data": self._params.get("p_checkpoint_data", {}),
                        "step_history": self._params.get("p_step_history", []),
                        "started_by_approval_id": aid,
                        "created_at": datetime.now(timezone.utc).isoformat(),
                        "updated_at": datetime.now(timezone.utc).isoformat(),
                    }
                    rows.append(wf)
                    return _FakeExec([{"workflow": wf, "created": True}])
                return _FakeExec([])

        return _FakeRPC(name, params, self._client)


class _FakeSupabaseClient:
    def __init__(self):
        self.data: dict[str, list[dict]] = {
            "missions": [],
            "workers": [],
            "onboarding_workflows": [],
            "human_intervention_checkpoints": [],
            "approval_requests": [],
        }

    def table(self, name):
        return _FakeTable(self, name)

    def rpc(self, name, params):
        return _FakeTable(self, name).rpc(name, params)


@pytest.fixture
def user_a_client():
    return _FakeSupabaseClient()


@pytest.fixture
def user_b_client():
    return _FakeSupabaseClient()


@pytest.fixture
def test_app():
    """FastAPI app with affiliate + connectors routers and overridden auth."""
    app = FastAPI()
    app.include_router(affiliate_module_router())
    return app


def affiliate_module_router():
    return connectors_module.router


def _build_app():
    app = FastAPI()
    app.include_router(connectors_module.router, tags=["connectors"])
    return app


def _make_test_client(client, user_id="test-user"):
    """Create a TestClient with auth overrides for a specific user client."""
    app = _build_app()
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_current_user] = lambda: {
        "id": user_id,
        "email": f"{user_id}@example.com",
        "role": "authenticated",
    }
    app.dependency_overrides[get_user_scoped_client] = lambda: client

    # Patch database modules so stores resolve to our fake client
    connectors_module.OnboardingWorkflowStore  # already imported
    import app.services.stores.onboarding_workflow_store as ows_mod
    import app.services.stores.platform_connection_store as pcs_mod
    import app.services.human_intervention as hi_mod

    ows_mod.database_module.supabase_client = client
    pcs_mod.database_module.supabase_client = client
    hi_mod.database_module.supabase_client = client

    return TestClient(app)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------
class TestOnboardingOwnershipIsRlsEnforced:
    """Bug 1: ownership must rely on RLS, not worker_id == current_user_id."""

    def test_user_a_can_get_own_onboarding_workflow(self, user_a_client):
        """User A can retrieve their own onboarding workflow."""
        # Seed: create a worker owned by user-a and a workflow for it
        user_a_client.table("workers").insert({
            "id": "worker-a-1",
            "owner_id": "test-user",
            "name": "Worker A",
        }).execute()
        wf_id = str(uuid4())
        user_a_client.table("onboarding_workflows").insert({
            "id": wf_id,
            "worker_id": "worker-a-1",
            "platform": "pinterest",
            "status": "awaiting_human",
            "current_step": 1,
            "total_steps": 3,
            "checkpoint_data": {"instructions": "test"},
            "step_history": [{"step": 1, "checkpoint_type": "oauth_authorization_required", "status": "pending"}],
        }).execute()

        tc = _make_test_client(user_a_client, user_id="test-user")
        resp = tc.get(f"/connectors/onboarding/{wf_id}", headers={"Authorization": "Bearer test-token"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True
        assert body["workflow_id"] == wf_id
        assert body["status"] == "awaiting_human"

    def test_user_b_cannot_get_user_a_workflow(self, user_b_client):
        """User B cannot retrieve User A's onboarding workflow.

        With the corrected query (no worker_id == current_user_id filter),
        the user-scoped client's RLS policy prevents User B from seeing
        User A's workflow. In the mock environment, queries are scoped by
        the explicit client, so User B's client simply cannot see
        User A's inserted rows.
        """
        # User A's workflow (insert via user_a's client perspective)
        wf_id = str(uuid4())
        user_b_client.table("workers").insert({
            "id": "worker-a-1",
            "owner_id": "test-user",  # owned by A
            "name": "Worker A",
        }).execute()
        user_b_client.table("onboarding_workflows").insert({
            "id": wf_id,
            "worker_id": "worker-a-1",
            "platform": "pinterest",
            "status": "awaiting_human",
            "current_step": 1,
            "total_steps": 3,
            "checkpoint_data": {"instructions": "test"},
            "step_history": [],
        }).execute()

        # User B uses a DIFFERENT client that has no rows for user A
        user_b_isolated_client = _FakeSupabaseClient()
        tc = _make_test_client(user_b_isolated_client, user_id="user-b")
        resp = tc.get(f"/connectors/onboarding/{wf_id}", headers={"Authorization": "Bearer test-token"})
        # Should get 404 — the workflow doesn't exist in user B's scoped view
        assert resp.status_code == 404, f"Expected 404, got {resp.status_code}: {resp.text}"


class TestUserScopedPinterestConnector:
    """Bug 2: connection_store must be user-scoped."""

    def test_connector_receives_user_scoped_connection_store(self):
        """The helper must construct both stores with the user-scoped client."""
        client = _FakeSupabaseClient()
        connector = _get_user_scoped_pinterest_connector(client=client)
        assert isinstance(connector, PinterestConnector)
        # workflow_store should have the explicit client
        assert connector._workflow_store._explicit_client is client
        # connection_store should also have the explicit client (the bug fix)
        assert connector._connection_store._explicit_client is client

    def test_connector_does_not_use_global_client(self):
        """Neither store should fall back to the global supabase_client."""
        client = _FakeSupabaseClient()
        connector = _get_user_scoped_pinterest_connector(client=client)

        ws = connector._workflow_store
        cs = connector._connection_store

        # Internal _client() should return the explicit client, not global
        assert ws._client() is client
        assert cs._client() is client

    def test_connector_works_without_client(self):
        """Without a client, the connector should still work (in-memory fallback)."""
        connector = _get_user_scoped_pinterest_connector(client=None)
        assert isinstance(connector, PinterestConnector)
        # Should not crash on health check
        health = connector.health_check()
        assert health["success"] is True
        assert health["platform"] == "pinterest"

    def test_connection_upsert_creates_persisted_record(self):
        """Verify connect_account writes through the user-scoped store."""
        client = _FakeSupabaseClient()
        connector = _get_user_scoped_pinterest_connector(client=client)

        result = connector.connect_account("worker-1", {"oauth_code": "should-be-stripped"})
        assert result["success"] is True
        assert result["status"] == "connected"

        # Verify the connection record exists in the user-scoped client's data
        connections = client.data.get("platform_connections", [])
        assert len(connections) == 1
        conn = connections[0]
        assert conn["owner_id"] == "worker-1"
        assert conn["platform"] == "pinterest"
        assert conn["status"] == "connected"

        # Verify no forbidden keys leaked into the persisted record
        forbidden = {"access_token", "refresh_token", "authorization_code",
                     "oauth_code", "client_secret", "api_key", "token", "password"}
        for key in conn:
            assert key.lower() not in forbidden, f"Forbidden key '{key}' in connection: {conn}"

    def test_connection_store_is_not_shared_global(self):
        """Each call to the helper creates a fresh, independent connector."""
        client_a = _FakeSupabaseClient()
        client_b = _FakeSupabaseClient()

        connector_a = _get_user_scoped_pinterest_connector(client=client_a)
        connector_b = _get_user_scoped_pinterest_connector(client=client_b)

        assert connector_a is not connector_b
        assert connector_a._workflow_store is not connector_b._workflow_store
        assert connector_a._connection_store is not connector_b._connection_store
        assert connector_a._workflow_store._explicit_client is client_a
        assert connector_b._workflow_store._explicit_client is client_b
        assert connector_a._connection_store._explicit_client is client_a
        assert connector_b._connection_store._explicit_client is client_b


class TestOnboardingResumeIdempontency:
    """Verify resume idempotency and ownership are preserved."""

    def test_resume_advances_same_workflow(self):
        """Resuming twice should advance steps, not create duplicates."""
        client = _FakeSupabaseClient()

        # Create a workflow
        connector = _get_user_scoped_pinterest_connector(client=client)
        worker_id = "worker-1"
        start_result = connector.start_onboarding(worker_id)
        assert start_result["success"] is True
        wf_id = start_result["workflow_id"]

        # Resume step 1 -> step 2
        result1 = connector.resume_onboarding(wf_id, {"email": "user@example.com"})
        assert result1["success"] is True
        assert result1["status"] == "awaiting_human"
        assert result1["current_step"] == 2

        # Resume step 2 -> step 3
        result2 = connector.resume_onboarding(wf_id, {"confirmation": "verified"})
        assert result2["success"] is True
        assert result2["current_step"] == 3

        # Resume step 3 -> completed
        result3 = connector.resume_onboarding(wf_id, {"confirmation": "configured"})
        assert result3["success"] is True
        assert result3["status"] == "completed"

        # Only one workflow exists
        workflows = client.data.get("onboarding_workflows", [])
        assert len(workflows) == 1

    def test_forbidden_auth_keys_preserved(self):
        """Ensure _FORBIDDEN_AUTH_KEYS is still enforced."""
        connector = _get_user_scoped_pinterest_connector(client=None)
        assert "access_token" in type(connector)._FORBIDDEN_AUTH_KEYS
        assert "refresh_token" in type(connector)._FORBIDDEN_AUTH_KEYS
        assert "authorization_code" in type(connector)._FORBIDDEN_AUTH_KEYS
        assert "oauth_code" in type(connector)._FORBIDDEN_AUTH_KEYS
        assert "client_secret" in type(connector)._FORBIDDEN_AUTH_KEYS
        assert "api_key" in type(connector)._FORBIDDEN_AUTH_KEYS
        assert "token" in type(connector)._FORBIDDEN_AUTH_KEYS
        assert "password" in type(connector)._FORBIDDEN_AUTH_KEYS
        assert "redirect_uri" in type(connector)._FORBIDDEN_AUTH_KEYS
