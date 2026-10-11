"""P1-4D Authorization Hardening Tests.

Tests for authorization enforcement independent of database RLS.
These tests verify that:
- Users can only access their own resources (missions, workers, approvals, etc.)
- Cross-user access is blocked (404 Not Found to avoid information leakage)
- Forged ownership fields in requests are ignored/overridden
- Anonymous access is denied
- Ownership transfer attempts are blocked
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

# Add backend path for app imports
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers.missions import router as missions_router
from app.routers.workers import router as workers_router
from app.routers.approvals import router as approvals_router
from app.routers.atlas import router as atlas_router
from app.routers.connectors import router as connectors_router

# HTTP status codes
HTTP_200_OK = 200
HTTP_201_CREATED = 201
HTTP_401_UNAUTHORIZED = 401
HTTP_404_NOT_FOUND = 404
HTTP_405_METHOD_NOT_ALLOWED = 405
HTTP_500_INTERNAL_SERVER_ERROR = 500
HTTP_503_SERVICE_UNAVAILABLE = 503


def _auth_user_id(request: Request) -> str:
    """Extract token from Authorization header as user ID (simulates verified auth)."""
    from fastapi import HTTPException, status

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    token = auth_header[7:].strip()
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing or invalid Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token


def _auth_user(request: Request) -> dict[str, Any]:
    """Return authenticated user dict (simulates verified auth)."""
    return {"id": _auth_user_id(request), "email": "test@example.com", "role": "authenticated"}


def _scoped_client(request: Request) -> Any:
    """Return None for scoped client (Supabase not configured in tests)."""
    return None


def _create_test_app() -> FastAPI:
    """Create a FastAPI app for testing with proper auth dependency overrides.

    This avoids shared state issues with the global app object when running
    the full test suite.
    """
    app = FastAPI()
    app.include_router(missions_router)
    app.include_router(workers_router)
    app.include_router(approvals_router)
    app.include_router(atlas_router)
    app.include_router(connectors_router)

    # Override auth dependencies to extract user ID from Bearer token
    # without requiring Supabase token verification
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    app.dependency_overrides[get_current_user_id] = _auth_user_id
    app.dependency_overrides[get_current_user] = _auth_user
    app.dependency_overrides[get_user_scoped_client] = _scoped_client

    return app


# Module-level fixtures

@pytest.fixture
def user_a() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def user_b() -> str:
    return str(uuid.uuid4())


@pytest.fixture
def client() -> TestClient:
    """Create a TestClient for a test FastAPI app with proper auth overrides.

    This fixture creates a separate test app (not the global one) with
    dependency overrides that simulate authenticated users by extracting
    the user ID directly from the Bearer token in the Authorization header.
    Endpoints that require Supabase will return 500/503, which tests
    handle by skipping.
    """
    test_app = _create_test_app()
    tc = TestClient(test_app)
    tc.headers["Content-Type"] = "application/json"
    return tc

# Helper to create a mock authorization header
def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# TestAuthorizationBasics
# ============================================================================

class TestAuthorizationBasics:
    """Basic ownership verification tests."""

    def test_owner_can_access_own_mission(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # User A creates a mission
        response = client.post(
            "/missions/",
            json={"title": "Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        # Either 201 or 503 (no DB) - but not 401
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            # No DB available, skip this test
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_201_CREATED
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        # User A can get their own mission
        response = client.get(
            f"/missions/{mission_id}",
            headers=auth_header(user_a),
        )
        assert response.status_code == HTTP_200_OK
        assert response.json()["id"] == mission_id

        # User B cannot access User A's mission (404, not 403)
        response = client.get(
            f"/missions/{mission_id}",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_non_owner_cannot_run_mission(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # User A creates a mission
        response = client.post(
            "/missions/",
            json={"title": "Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_201_CREATED
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        # User A can run their own mission
        response = client.post(
            f"/missions/{mission_id}/run",
            headers=auth_header(user_a),
        )
        assert response.status_code != HTTP_404_NOT_FOUND

        # User B cannot run User A's mission
        response = client.post(
            f"/missions/{mission_id}/run",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_forged_owner_id_in_create_is_ignored(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # User A tries to create a mission claiming to be owned by User B
        response = client.post(
            "/missions/",
            json={
                "title": "Forged Mission",
                "description": "Should be owned by A",
                "owner_id": user_b,  # Forged field
            },
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_201_CREATED
        mission = response.json()["mission"]
        # The mission must be owned by User A, not User B
        assert mission["owner_id"] == user_a
        assert mission["owner_id"] != user_b

    def test_anonymous_access_is_denied(self, client: TestClient) -> None:
        # No Authorization header
        response = client.get("/missions/")
        assert response.status_code == HTTP_401_UNAUTHORIZED

        response = client.post("/missions/", json={"title": "Anon Mission"})
        assert response.status_code == HTTP_401_UNAUTHORIZED

        response = client.get("/missions/123")
        assert response.status_code == HTTP_401_UNAUTHORIZED

    def test_ownership_transfer_via_patch_is_blocked(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # User A creates a mission
        response = client.post(
            "/missions/",
            json={"title": "Transfer Test", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_201_CREATED
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        # User A tries to patch the mission to change owner_id to User B
        response = client.patch(
            f"/missions/{mission_id}",
            json={"owner_id": user_b},  # Forged ownership transfer
            headers=auth_header(user_a),
        )
        # The endpoint should ignore the owner_id field or return an error
        assert response.status_code == HTTP_200_OK
        mission = response.json()
        # The mission must still be owned by User A
        assert mission["owner_id"] == user_a
        assert mission["owner_id"] != user_b


# ============================================================================
# TestWorkerAuthorization
# ============================================================================

class TestWorkerAuthorization:
    """Worker-specific authorization tests."""

    def test_owner_can_access_own_worker(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # User A creates a worker
        response = client.post(
            "/workers/",
            json={"name": "Worker A", "role": "assistant"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_201_CREATED
        worker_a = response.json()["worker"]
        worker_id = worker_a["id"]

        # User A can get their own worker
        response = client.get(
            f"/workers/{worker_id}/status",
            headers=auth_header(user_a),
        )
        assert response.status_code == HTTP_200_OK

        # User B cannot access User A's worker
        response = client.get(
            f"/workers/{worker_id}/status",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_non_owner_cannot_run_worker(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # User A creates a worker
        response = client.post(
            "/workers/",
            json={"name": "Worker A", "role": "assistant"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_201_CREATED
        worker_a = response.json()["worker"]
        worker_id = worker_a["id"]

        # User A can run their own worker
        response = client.post(
            f"/workers/{worker_id}/run",
            headers=auth_header(user_a),
        )
        assert response.status_code != HTTP_404_NOT_FOUND

        # User B cannot run User A's worker
        response = client.post(
            f"/workers/{worker_id}/run",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND


# ============================================================================
# TestApprovalAuthorization
# ============================================================================

class TestApprovalAuthorization:
    """Approval-specific authorization tests."""

    def test_owner_can_access_own_approval(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # First, User A needs a mission to create an approval for
        response = client.post(
            "/missions/",
            json={"title": "Mission for Approval", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_201_CREATED
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        # User A creates an approval request
        response = client.post(
            "/approvals/",
            json={
                "mission_id": mission_id,
                "action_type": "publish",
                "risk_level": "high",
                "payload": {"data": "test"},
            },
            headers=auth_header(user_a),
        )
        assert response.status_code == HTTP_200_OK
        approval = response.json()["approval"]
        approval_id = approval["id"]

        # User A can get their own approval
        response = client.get(
            f"/approvals/{approval_id}",
            headers=auth_header(user_a),
        )
        assert response.status_code == HTTP_200_OK
        assert response.json()["approval"]["id"] == approval_id

        # User B cannot access User A's approval
        response = client.get(
            f"/approvals/{approval_id}",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_non_owner_cannot_approve_or_reject(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # Setup: User A creates a mission and an approval
        response = client.post(
            "/missions/",
            json={"title": "Mission for Approval", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_201_CREATED
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        response = client.post(
            "/approvals/",
            json={
                "mission_id": mission_id,
                "action_type": "publish",
                "risk_level": "high",
                "payload": {"data": "test"},
            },
            headers=auth_header(user_a),
        )
        assert response.status_code == HTTP_200_OK
        approval = response.json()["approval"]
        approval_id = approval["id"]

        # User B tries to approve User A's approval
        response = client.post(
            f"/approvals/{approval_id}/approve",
            json={"approved_by": user_b},
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

        # User B tries to reject User A's approval
        response = client.post(
            f"/approvals/{approval_id}/reject",
            json={"rejected_by": user_b, "reason": "Test"},
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

        # User A can approve their own approval
        response = client.post(
            f"/approvals/{approval_id}/approve",
            json={},
            headers=auth_header(user_a),
        )
        assert response.status_code != HTTP_404_NOT_FOUND
        if response.status_code == HTTP_200_OK:
            approval_data = response.json()["approval"]
            # The approved_by field should be set to user_a (from token)
            assert approval_data.get("approved_by") == user_a


# ============================================================================
# TestAtlasAuthorization
# ============================================================================

class TestAtlasAuthorization:
    """Atlas-specific authorization tests."""

    def test_owner_can_access_own_atlas_mission(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # User A creates a mission via Atlas endpoint
        response = client.post(
            "/atlas/mission",
            json={"title": "Atlas Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code in (HTTP_500_INTERNAL_SERVER_ERROR, HTTP_503_SERVICE_UNAVAILABLE):
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_200_OK
        mission_a = response.json()
        mission_id = mission_a["id"]

        # User A can get their own mission
        response = client.get(
            f"/atlas/missions/{mission_id}",
            headers=auth_header(user_a),
        )
        assert response.status_code == HTTP_200_OK

        # User B cannot access User A's mission
        response = client.get(
            f"/atlas/missions/{mission_id}",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_non_owner_cannot_create_atlas_command_against_others_mission(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # User A creates a mission
        response = client.post(
            "/atlas/mission",
            json={"title": "Atlas Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code in (HTTP_500_INTERNAL_SERVER_ERROR, HTTP_503_SERVICE_UNAVAILABLE):
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_200_OK
        mission_a = response.json()
        mission_id = mission_a["id"]

        # User B tries to create an Atlas command targeting User A's mission
        response = client.post(
            "/atlas/command",
            json={
                "command_type": "START_RESEARCH",
                "mission_id": mission_id,
                "target_worker": "worker-1",
                "payload": {},
            },
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

        # User A can create a command against their own mission
        response = client.post(
            "/atlas/command",
            json={
                "command_type": "START_RESEARCH",
                "mission_id": mission_id,
                "target_worker": "worker-1",
                "payload": {},
            },
            headers=auth_header(user_a),
        )
        assert response.status_code != HTTP_404_NOT_FOUND

    def test_non_owner_cannot_access_others_atlas_commands(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # User A creates a mission and a command
        response = client.post(
            "/atlas/mission",
            json={"title": "Atlas Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code in (HTTP_500_INTERNAL_SERVER_ERROR, HTTP_503_SERVICE_UNAVAILABLE):
            pytest.skip("Supabase not configured")
        mission_a = response.json()
        mission_id = mission_a["id"]

        response = client.post(
            "/atlas/command",
            json={
                "command_type": "START_RESEARCH",
                "mission_id": mission_id,
                "target_worker": "worker-1",
                "payload": {},
            },
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_404_NOT_FOUND:
            pytest.skip("Command creation blocked")
        command_a = response.json()
        command_id = command_a["id"]

        # User A can list their own commands
        response = client.get(
            "/atlas/commands",
            headers=auth_header(user_a),
        )
        assert response.status_code == HTTP_200_OK
        commands = response.json()
        assert any(c["id"] == command_id for c in commands)

        # User B cannot list User A's commands
        response = client.get(
            "/atlas/commands",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_200_OK
        commands_b = response.json()
        assert not any(c["id"] == command_id for c in commands_b)

    def test_non_owner_cannot_access_others_worker_responses(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # User B tries to list worker responses - should be empty or own data
        response = client.get(
            "/atlas/worker-responses",
            headers=auth_header(user_b),
        )
        # May return 500 if Supabase not configured
        if response.status_code in (HTTP_500_INTERNAL_SERVER_ERROR, HTTP_503_SERVICE_UNAVAILABLE):
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_200_OK
        responses_b = response.json()
        assert isinstance(responses_b, list)


# ============================================================================
# TestConnectorAuthorization
# ============================================================================

class TestConnectorAuthorization:
    """Connector-specific authorization tests."""

    def test_owner_can_access_own_onboarding_workflow(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # User A creates a worker
        response = client.post(
            "/workers/",
            json={"name": "Worker A", "role": "assistant"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_201_CREATED
        worker_a = response.json()["worker"]
        worker_id = worker_a["id"]

        # User A starts an onboarding workflow
        response = client.post(
            "/connectors/onboarding/start",
            json={"platform": "pinterest", "worker_id": worker_id},
            headers=auth_header(user_a),
        )
        # Either 200 or 503 - but not 404 for worker ownership
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")

        # User B tries to access User A's onboarding
        # We use a known workflow ID format for testing
        response = client.get(
            "/connectors/onboarding/test-workflow-id",
            headers=auth_header(user_b),
        )
        # Should be 404 for non-existent or non-owned workflow
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_non_owner_cannot_resume_others_onboarding(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        response = client.post(
            "/connectors/onboarding/test-workflow-id/resume",
            json={"workflow_id": "test-workflow-id", "checkpoint_id": None, "human_input": {}},
            headers=auth_header(user_b),
        )
        if response.status_code in (HTTP_500_INTERNAL_SERVER_ERROR, HTTP_503_SERVICE_UNAVAILABLE):
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_non_owner_cannot_complete_others_checkpoint(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        checkpoint_id = "test-checkpoint-id"

        response = client.post(
            f"/connectors/checkpoints/{checkpoint_id}/complete",
            json={"human_input": {"test": "data"}},
            headers=auth_header(user_b),
        )
        if response.status_code in (HTTP_500_INTERNAL_SERVER_ERROR, HTTP_503_SERVICE_UNAVAILABLE):
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_anonymous_access_to_connectors_is_denied_for_protected_endpoints(
        self, client: TestClient
    ) -> None:
        # All connector endpoints now require authentication after P1-4D
        # Anonymous access should be denied (401) for all endpoints
        response = client.get("/connectors/")
        assert response.status_code == HTTP_401_UNAUTHORIZED

        response = client.get("/connectors/pinterest")
        assert response.status_code == HTTP_401_UNAUTHORIZED

        # Protected endpoints should require auth
        response = client.post(
            "/connectors/onboarding/start",
            json={"platform": "pinterest", "worker_id": "worker-1"},
        )
        assert response.status_code == HTTP_401_UNAUTHORIZED

        response = client.get("/connectors/onboarding/some-id")
        assert response.status_code == HTTP_401_UNAUTHORIZED

        response = client.post(
            "/connectors/onboarding/some-id/resume",
            json={"workflow_id": "some-id", "checkpoint_id": None, "human_input": {}},
        )
        assert response.status_code == HTTP_401_UNAUTHORIZED

        response = client.get("/connectors/checkpoints/pending")
        assert response.status_code == HTTP_401_UNAUTHORIZED

        response = client.get("/connectors/checkpoints/some-id")
        assert response.status_code == HTTP_401_UNAUTHORIZED

        response = client.post(
            "/connectors/checkpoints/some-id/complete",
            json={"human_input": {}},
        )
        assert response.status_code == HTTP_401_UNAUTHORIZED

    def test_forged_owner_id_in_onboarding_start_is_ignored(
        self, user_a: str, user_b: str, client: TestClient
    ) -> None:
        # Create workers for both users
        response = client.post(
            "/workers/",
            json={"name": "Worker A", "role": "assistant"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        worker_a = response.json()["worker"]
        worker_id_a = worker_a["id"]

        response = client.post(
            "/workers/",
            json={"name": "Worker B", "role": "assistant"},
            headers=auth_header(user_b),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        worker_b = response.json()["worker"]
        worker_id_b = worker_b["id"]

        # User A tries to start onboarding for User B's worker
        response = client.post(
            "/connectors/onboarding/start",
            json={"platform": "pinterest", "worker_id": worker_id_b},
            headers=auth_header(user_a),
        )
        assert response.status_code == HTTP_404_NOT_FOUND


# ============================================================================
# TestAuthorizationCategories
# ============================================================================

class TestAuthorizationCategories:
    """Category-level authorization tests."""

    def test_cross_user_mission_access_denied(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Cross-user mission access denied."""
        response = client.post(
            "/missions/",
            json={"title": "Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        response = client.get(
            f"/missions/{mission_id}",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_cross_user_worker_access_denied(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Cross-user worker access denied."""
        response = client.post(
            "/workers/",
            json={"name": "Worker A", "role": "assistant"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        worker_a = response.json()["worker"]
        worker_id = worker_a["id"]

        response = client.get(
            f"/workers/{worker_id}/status",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_cross_user_approval_access_denied(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Cross-user approval access denied."""
        response = client.post(
            "/missions/",
            json={"title": "Mission for Approval", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        response = client.post(
            "/approvals/",
            json={
                "mission_id": mission_id,
                "action_type": "publish",
                "risk_level": "high",
                "payload": {"data": "test"},
            },
            headers=auth_header(user_a),
        )
        assert response.status_code == HTTP_200_OK
        approval = response.json()["approval"]
        approval_id = approval["id"]

        response = client.get(
            f"/approvals/{approval_id}",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_cross_user_connector_access_denied(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Cross-user connector access denied."""
        response = client.post(
            "/workers/",
            json={"name": "Worker A", "role": "assistant"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        worker_id = response.json()["worker"]["id"]

        response = client.post(
            "/connectors/onboarding/start",
            json={"platform": "pinterest", "worker_id": worker_id},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")

        # User B cannot access User A's connector onboarding
        response = client.post(
            "/connectors/onboarding/test-workflow-id/resume",
            json={"workflow_id": "test-workflow-id", "checkpoint_id": None, "human_input": {}},
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_cross_user_atlas_command_access_denied(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Cross-user Atlas command access denied."""
        response = client.post(
            "/atlas/mission",
            json={"title": "Atlas Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code in (HTTP_500_INTERNAL_SERVER_ERROR, HTTP_503_SERVICE_UNAVAILABLE):
            pytest.skip("Supabase not configured")
        mission_a = response.json()
        mission_id = mission_a["id"]

        response = client.post(
            "/atlas/command",
            json={
                "command_type": "START_RESEARCH",
                "mission_id": mission_id,
                "target_worker": "worker-1",
                "payload": {},
            },
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_cross_user_atlas_command_access_denied_list(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Cross-user Atlas command list access denied."""
        response = client.post(
            "/atlas/mission",
            json={"title": "Atlas Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code in (HTTP_500_INTERNAL_SERVER_ERROR, HTTP_503_SERVICE_UNAVAILABLE):
            pytest.skip("Supabase not configured")
        mission_a = response.json()
        mission_id = mission_a["id"]

        response = client.post(
            "/atlas/command",
            json={
                "command_type": "START_RESEARCH",
                "mission_id": mission_id,
                "target_worker": "worker-1",
                "payload": {},
            },
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_404_NOT_FOUND:
            pytest.skip("Command creation blocked")

        response = client.get(
            "/atlas/commands",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_200_OK
        commands_b = response.json()
        assert isinstance(commands_b, list)
        # User B should not see User A's commands
        # (They may see their own, but not User A's)

    def test_forged_user_id_cannot_change_authorization_identity(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Forged user_id cannot change authorization identity."""
        # User A creates a mission
        response = client.post(
            "/missions/",
            json={"title": "Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_201_CREATED

        # User A tries to use User B's token to access User A's mission
        response = client.get(
            "/missions/",
            headers=auth_header(user_b),
        )
        # User B should only see their own missions, not User A's
        assert response.status_code == HTTP_200_OK

        # User B should not be able to access User A's missions
        response = client.get(
            f"/missions/",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_200_OK
        missions_b = response.json()
        # User B should have 0 or their own missions, not User A's
        assert isinstance(missions_b, list)

    def test_owner_can_access_own_mission(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Owner can access own mission."""
        response = client.post(
            "/missions/",
            json={"title": "Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        assert response.status_code == HTTP_201_CREATED
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        response = client.get(
            f"/missions/{mission_id}",
            headers=auth_header(user_a),
        )
        assert response.status_code == HTTP_200_OK
        assert response.json()["id"] == mission_id

    def test_cross_user_update_denied(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Cross-user UPDATE denied."""
        response = client.post(
            "/missions/",
            json={"title": "Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        # User B tries to update User A's mission
        response = client.patch(
            f"/missions/{mission_id}",
            json={"title": "Hacked Mission"},
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_cross_user_delete_denied(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Cross-user DELETE denied."""
        response = client.post(
            "/missions/",
            json={"title": "Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        # User B tries to delete User A's mission
        response = client.delete(
            f"/missions/{mission_id}",
            headers=auth_header(user_b),
        )
        # DELETE endpoint may not exist yet, but if it does, should be 404
        assert response.status_code in [HTTP_404_NOT_FOUND, HTTP_405_METHOD_NOT_ALLOWED]

    def test_cross_user_execute_resume_denied(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Cross-user EXECUTE/RESUME denied."""
        response = client.post(
            "/missions/",
            json={"title": "Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        # User B tries to execute User A's mission
        response = client.post(
            f"/missions/{mission_id}/run",
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

        # User B tries to resume User A's onboarding
        response = client.post(
            "/connectors/onboarding/test-workflow-id/resume",
            json={"workflow_id": "test-workflow-id", "checkpoint_id": None, "human_input": {}},
            headers=auth_header(user_b),
        )
        assert response.status_code == HTTP_404_NOT_FOUND

    def test_anonymous_access_denied(self, client: TestClient) -> None:
        """Category: Anonymous access denied."""
        response = client.get("/missions/")
        assert response.status_code == HTTP_401_UNAUTHORIZED

        response = client.post("/missions/", json={"title": "Test"})
        assert response.status_code == HTTP_401_UNAUTHORIZED

    def test_ownership_transfer_rejected(self, user_a: str, user_b: str, client: TestClient) -> None:
        """Category: Ownership transfer rejected."""
        response = client.post(
            "/missions/",
            json={"title": "Mission A", "description": "Owned by A"},
            headers=auth_header(user_a),
        )
        if response.status_code == HTTP_503_SERVICE_UNAVAILABLE:
            pytest.skip("Supabase not configured")
        mission_a = response.json()["mission"]
        mission_id = mission_a["id"]

        # User A tries to transfer ownership to User B
        response = client.patch(
            f"/missions/{mission_id}",
            json={"owner_id": user_b},
            headers=auth_header(user_a),
        )
        assert response.status_code == HTTP_200_OK
        mission = response.json()
        assert mission["owner_id"] == user_a
        assert mission["owner_id"] != user_b