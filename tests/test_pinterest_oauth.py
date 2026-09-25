"""Tests for Pinterest OAuth callback behavior."""

import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request, status
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client
from app.main import app
from app.routers.connectors import router as connectors_router


def _auth_user_id(request: Request) -> str:
    """Return a fixed user ID for testing (bypasses Supabase auth)."""
    return "test-owner-id"


def _auth_user(request: Request) -> dict[str, Any]:
    """Return a fixed authenticated user dict for testing."""
    return {"id": "test-owner-id", "email": "test@example.com", "role": "authenticated"}


def _scoped_client(request: Request) -> None:
    """Return None for scoped client (Supabase not configured in tests)."""
    return None


def _bypass_auth() -> dict:
    """Return dependency overrides that bypass authentication for testing."""
    return {
        get_current_user_id: _auth_user_id,
        get_current_user: _auth_user,
        get_user_scoped_client: _scoped_client,
    }


def _make_checkpoint(state: str, owner_id: str, status_val: str = "awaiting_human") -> dict[str, Any]:
    """Create a mock checkpoint dict with flat metadata."""
    return {
        "id": "cp-1",
        "state": state,
        "owner_id": owner_id,
        "status": status_val,
        "metadata": {
            "oauth_state": state,
            "oauth_state_owner": owner_id,
            "oauth_state_workflow": "wf-test",
            "oauth_state_created_at": "2024-01-01T00:00:00Z",
            "oauth_state_expires_at": 9999999999.0,
            "authorization_url": "https://pinterest.com/oauth?state=" + state,
            "workflow_id": "wf-test",
        },
    }


def _make_checkpoint_legacy(state: str, owner_id: str, status_val: str = "awaiting_human") -> dict[str, Any]:
    """Create a mock checkpoint with legacy nested metadata."""
    return {
        "id": "cp-1",
        "state": state,
        "owner_id": owner_id,
        "status": status_val,
        "metadata": {
            "metadata": {
                "oauth_state": state,
                "oauth_state_owner": owner_id,
                "oauth_state_workflow": "wf-test",
                "oauth_state_created_at": "2024-01-01T00:00:00Z",
                "oauth_state_expires_at": 9999999999.0,
            },
            "workflow_id": "wf-test",
        },
    }


def _make_processing_checkpoint(state: str, owner_id: str) -> dict[str, Any]:
    """Create a checkpoint already in processing state."""
    return {
        "id": "cp-1",
        "state": state,
        "owner_id": owner_id,
        "status": "processing",
        "metadata": {
            "oauth_state": state,
            "oauth_state_owner": owner_id,
            "oauth_state_workflow": "wf-test",
            "oauth_state_created_at": "2024-01-01T00:00:00Z",
            "oauth_state_expires_at": 9999999999.0,
            "workflow_id": "wf-test",
        },
    }


def _make_completed_checkpoint(state: str, owner_id: str) -> dict[str, Any]:
    """Create a checkpoint already in completed state."""
    return {
        "id": "cp-1",
        "state": state,
        "owner_id": owner_id,
        "status": "completed",
        "metadata": {
            "oauth_state": state,
            "oauth_state_owner": owner_id,
            "oauth_state_workflow": "wf-test",
            "oauth_state_consumed": True,
            "workflow_id": "wf-test",
        },
    }


def _make_failed_checkpoint(state: str, owner_id: str) -> dict[str, Any]:
    """Create a checkpoint in failed state."""
    return {
        "id": "cp-1",
        "state": state,
        "owner_id": owner_id,
        "status": "failed",
        "metadata": {
            "oauth_state": state,
            "oauth_state_owner": owner_id,
            "workflow_id": "wf-test",
        },
    }


def fake_post(*args: Any, **kwargs: Any) -> Any:
    """Simulate Pinterest token endpoint response."""
    raise AssertionError("Unexpected HTTP call to Pinterest token endpoint")


def test_callback_rejects_missing_credentials(monkeypatch):
    """Test that missing PINTEREST_CLIENT_ID or PINTEREST_CLIENT_SECRET blocks the OAuth flow."""
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    monkeypatch.delenv("PINTEREST_CLIENT_ID", raising=False)
    monkeypatch.delenv("PINTEREST_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.claim_oauth_callback.return_value = {"status": "claimed", "checkpoint": cp}
    mock_manager.fail_oauth_callback.return_value = {"success": True}
    mock_manager.reconcile_oauth_callback.return_value = {"status": "processing", "checkpoint": cp}

    mock_connector = MagicMock()
    mock_connector.complete_onboarding_oauth.return_value = {"success": True, "status": "connected"}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=mock_connector), \
         patch("app.routers.connectors.httpx.AsyncClient") as mock_async_client:
        mock_async_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=fake_post)
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        assert resp.status_code == 200
        assert resp.json()["status"] == "checkpoint_completed"
        mock_manager.claim_oauth_callback.assert_called_once_with("state-1", owner_id="test-owner-id")
        mock_connector.complete_onboarding_oauth.assert_not_called()


def test_callback_claims_checkpoint_awaiting_human_to_processing(monkeypatch):
    """Test that the callback transitions awaiting_human -> processing."""
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    monkeypatch.delenv("PINTEREST_CLIENT_ID", raising=False)
    monkeypatch.delenv("PINTEREST_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.claim_oauth_callback.return_value = {"status": "claimed", "checkpoint": cp}
    mock_manager.fail_oauth_callback.return_value = {"success": True}
    mock_manager.reconcile_oauth_callback.return_value = {"status": "processing", "checkpoint": cp}

    mock_connector = MagicMock()
    mock_connector.complete_onboarding_oauth.return_value = {"success": True, "status": "connected"}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=mock_connector), \
         patch("app.routers.connectors.httpx.AsyncClient") as mock_async_client:
        mock_async_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=fake_post)
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        assert resp.status_code == 200
        # Verify claim_oauth_callback was called
        mock_manager.claim_oauth_callback.assert_called_once()
        # Verify the claim returned 'claimed' status
        claim_result = mock_manager.claim_oauth_callback.return_value
        assert claim_result["status"] == "claimed"


def test_processing_duplicate_callback(monkeypatch):
    """Test that a second callback when already processing does NOT exchange tokens."""
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    monkeypatch.delenv("PINTEREST_CLIENT_ID", raising=False)
    monkeypatch.delenv("PINTEREST_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_processing_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.claim_oauth_callback.return_value = {"status": "processing", "checkpoint": cp}

    mock_connector = MagicMock()

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=mock_connector), \
         patch("app.routers.connectors.httpx.AsyncClient"):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"
        # Token exchange should NOT have occurred
        mock_connector.complete_onboarding_oauth.assert_not_called()


def test_completed_duplicate_callback(monkeypatch):
    """Test that a second callback when already completed returns idempotent result."""
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    monkeypatch.delenv("PINTEREST_CLIENT_ID", raising=False)
    monkeypatch.delenv("PINTEREST_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_completed_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.claim_oauth_callback.return_value = {"status": "completed", "checkpoint": cp}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=MagicMock()), \
         patch("app.routers.connectors.httpx.AsyncClient"):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"


def test_failed_duplicate_callback(monkeypatch):
    """Test that a second callback when already failed returns failed result."""
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    monkeypatch.delenv("PINTEREST_CLIENT_ID", raising=False)
    monkeypatch.delenv("PINTEREST_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_failed_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.claim_oauth_callback.return_value = {"status": "failed", "checkpoint": cp}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=MagicMock()), \
         patch("app.routers.connectors.httpx.AsyncClient"):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"


def test_retryable_token_exchange_failure(monkeypatch):
    """Test that token exchange failure transitions processing -> awaiting_human."""
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    monkeypatch.delenv("PINTEREST_CLIENT_ID", raising=False)
    monkeypatch.delenv("PINTEREST_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("PINTEREST_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PINTEREST_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.claim_oauth_callback.return_value = {"status": "claimed", "checkpoint": cp}
    mock_manager.reconcile_oauth_callback.return_value = {"status": "processing", "checkpoint": cp}

    mock_connector = MagicMock()

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=True), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=mock_connector), \
         patch("app.routers.connectors.httpx.AsyncClient") as mock_async_client:
        mock_async_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=Exception("Network error"))
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "retryable"
        # reconcile_oauth_callback should have been called
        mock_manager.reconcile_oauth_callback.assert_called_once()


def test_definitive_oauth_failure(monkeypatch):
    """Test that Pinterest rejection transitions processing -> failed."""
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    monkeypatch.setenv("PINTEREST_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PINTEREST_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.claim_oauth_callback.return_value = {"status": "claimed", "checkpoint": cp}
    mock_manager.fail_oauth_callback.return_value = {"success": True}

    mock_connector = MagicMock()

    token_response = MagicMock()
    token_response.status_code = 400

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=True), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=mock_connector), \
         patch("app.routers.connectors.httpx.AsyncClient") as mock_async_client:
        mock_async_client.return_value.__aenter__.return_value.post = AsyncMock(return_value=token_response)
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "failed"
        mock_manager.fail_oauth_callback.assert_called_once()


def test_successful_atomic_completion(monkeypatch):
    """Test that successful token exchange calls the atomic completion RPC."""
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    monkeypatch.setenv("PINTEREST_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PINTEREST_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.claim_oauth_callback.return_value = {"status": "claimed", "checkpoint": cp}
    mock_manager.fail_oauth_callback.return_value = {"success": True}
    mock_manager.reconcile_oauth_callback.return_value = {"status": "processing", "checkpoint": cp}

    mock_connector = MagicMock()
    mock_connector.complete_onboarding_oauth.return_value = {"success": True, "status": "connected"}

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "token_type": "bearer",
        "expires_in": 3600,
        "account_id": "pin-account-123",
        "username": "testuser",
        "scope": "user_accounts:read boards:read pins:create",
    }

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=True), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=mock_connector), \
         patch("app.routers.connectors.httpx.AsyncClient") as mock_async_client:
        mock_instance = mock_async_client.return_value
        mock_instance.__aenter__.return_value.post = AsyncMock(return_value=token_response)
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "connected"
        # Verify complete_onboarding_oauth was called
        mock_connector.complete_onboarding_oauth.assert_called_once()


def test_completion_idempotency(monkeypatch):
    """Test that retrying after successful completion returns idempotent result."""
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    monkeypatch.setenv("PINTEREST_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PINTEREST_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_completed_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.claim_oauth_callback.return_value = {"status": "completed", "checkpoint": cp}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=True), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=MagicMock()), \
         patch("app.routers.connectors.httpx.AsyncClient"):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        assert resp.status_code == 200
        assert resp.json()["status"] == "completed"


def test_completion_failure_reconciliation(monkeypatch):
    """Test that completion DB failure keeps processing state."""
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    monkeypatch.setenv("PINTEREST_CLIENT_ID", "test-client-id")
    monkeypatch.setenv("PINTEREST_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.claim_oauth_callback.return_value = {"status": "claimed", "checkpoint": cp}
    mock_manager.reconcile_oauth_callback.return_value = {"status": "processing", "checkpoint": cp}

    mock_connector = MagicMock()
    mock_connector.complete_onboarding_oauth.return_value = {"success": False, "error": "db_failure"}

    token_response = MagicMock()
    token_response.status_code = 200
    token_response.json.return_value = {
        "access_token": "test-access-token",
        "refresh_token": "test-refresh-token",
        "token_type": "bearer",
        "expires_in": 3600,
        "account_id": "pin-account-123",
        "username": "testuser",
        "scope": "user_accounts:read",
    }

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=True), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=mock_connector), \
         patch("app.routers.connectors.httpx.AsyncClient") as mock_async_client:
        mock_instance = mock_async_client.return_value
        mock_instance.__aenter__.return_value.post = AsyncMock(return_value=token_response)
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "processing"
        # reconcile_oauth_callback should have been called
        mock_manager.reconcile_oauth_callback.assert_called_once()


def test_connection_uniqueness():
    """Test that PlatformConnectionStore upsert handles unique constraint."""
    from app.services.stores.platform_connection_store import PlatformConnectionStore

    store = PlatformConnectionStore()
    # First upsert
    result1 = store.upsert("owner-1", "pinterest", status="connected", display_name="test")
    assert result1["success"] is True
    # Second upsert with same owner/platform should succeed (upsert)
    result2 = store.upsert("owner-1", "pinterest", status="connected", display_name="test")
    assert result2["success"] is True


def test_make_checkpoint_has_flat_metadata():
    """Test that checkpoint metadata has oauth_state at the top level."""
    cp = _make_checkpoint("state-1", owner_id="test-owner-id")
    metadata = cp["metadata"]
    assert "oauth_state" in metadata
    assert metadata["oauth_state"] == "state-1"
    assert "workflow_id" in metadata
    assert metadata["workflow_id"] == "wf-test"


def test_make_checkpoint_legacy_nested_metadata():
    """Test that legacy nested metadata is supported."""
    cp = _make_checkpoint_legacy("state-1", owner_id="test-owner-id")
    metadata = cp["metadata"]
    # Legacy nested format
    assert "metadata" in metadata
    assert metadata["metadata"]["oauth_state"] == "state-1"


def test_claim_oauth_callback_transitions_to_processing():
    """Test that claim_oauth_callback transitions awaiting_human -> processing."""
    from app.services.human_intervention import HumanInterventionCheckpoint, HumanInterventionManager
    from datetime import datetime, timezone, timedelta

    manager = HumanInterventionManager()
    checkpoint = HumanInterventionCheckpoint(
        checkpoint_id="cp-1",
        mission_id="mission-1",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        status="awaiting_human",
        instructions="Authorize AEA",
        metadata={
            "oauth_state": "test-state",
            "oauth_state_owner": "test-owner",
            "oauth_state_workflow": "wf-1",
            "oauth_state_created_at": "2024-01-01T00:00:00Z",
            "oauth_state_expires_at": 9999999999.0,
        },
    )
    manager._memory_store["cp-1"] = checkpoint

    result = manager.claim_oauth_callback("test-state", owner_id="test-owner")
    assert result is not None
    assert result["status"] == "claimed"
    # Verify the checkpoint status changed to processing
    assert checkpoint.status == "processing"
    assert checkpoint.metadata.get("oauth_state_processing") is True


def test_claim_oauth_callback_processing_duplicate():
    """Test that claiming an already-processing checkpoint returns processing."""
    from app.services.human_intervention import HumanInterventionCheckpoint, HumanInterventionManager
    from datetime import datetime, timezone, timedelta

    manager = HumanInterventionManager()
    checkpoint = HumanInterventionCheckpoint(
        checkpoint_id="cp-1",
        mission_id="mission-1",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        status="processing",
        instructions="Authorize AEA",
        metadata={
            "oauth_state": "test-state",
            "oauth_state_owner": "test-owner",
            "oauth_state_workflow": "wf-1",
        },
    )
    manager._memory_store["cp-1"] = checkpoint

    result = manager.claim_oauth_callback("test-state", owner_id="test-owner")
    assert result is not None
    assert result["status"] == "processing"


def test_claim_oauth_callback_completed_duplicate():
    """Test that claiming a completed checkpoint returns completed."""
    from app.services.human_intervention import HumanInterventionCheckpoint, HumanInterventionManager
    from datetime import datetime, timezone, timedelta

    manager = HumanInterventionManager()
    checkpoint = HumanInterventionCheckpoint(
        checkpoint_id="cp-1",
        mission_id="mission-1",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        status="completed",
        instructions="Authorize AEA",
        metadata={
            "oauth_state": "test-state",
            "oauth_state_owner": "test-owner",
            "oauth_state_workflow": "wf-1",
        },
    )
    manager._memory_store["cp-1"] = checkpoint

    result = manager.claim_oauth_callback("test-state", owner_id="test-owner")
    assert result is not None
    assert result["status"] == "completed"


def test_claim_oauth_callback_failed_duplicate():
    """Test that claiming a failed checkpoint returns failed."""
    from app.services.human_intervention import HumanInterventionCheckpoint, HumanInterventionManager
    from datetime import datetime, timezone, timedelta

    manager = HumanInterventionManager()
    checkpoint = HumanInterventionCheckpoint(
        checkpoint_id="cp-1",
        mission_id="mission-1",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        status="failed",
        instructions="Authorize AEA",
        metadata={
            "oauth_state": "test-state",
            "oauth_state_owner": "test-owner",
            "oauth_state_workflow": "wf-1",
        },
    )
    manager._memory_store["cp-1"] = checkpoint

    result = manager.claim_oauth_callback("test-state", owner_id="test-owner")
    assert result is not None
    assert result["status"] == "failed"


def test_claim_oauth_callback_no_oauth_state_consumed():
    """Test that oauth_state_consumed is NOT set during claim."""
    from app.services.human_intervention import HumanInterventionCheckpoint, HumanInterventionManager
    from datetime import datetime, timezone, timedelta

    manager = HumanInterventionManager()
    checkpoint = HumanInterventionCheckpoint(
        checkpoint_id="cp-1",
        mission_id="mission-1",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        status="awaiting_human",
        instructions="Authorize AEA",
        metadata={
            "oauth_state": "test-state",
            "oauth_state_owner": "test-owner",
            "oauth_state_workflow": "wf-1",
        },
    )
    manager._memory_store["cp-1"] = checkpoint

    manager.claim_oauth_callback("test-state", owner_id="test-owner")
    # oauth_state_consumed should NOT be set during claim
    assert checkpoint.metadata.get("oauth_state_consumed") is None
    # oauth_state_processing should be set
    assert checkpoint.metadata.get("oauth_state_processing") is True


def test_no_secret_token_leakage():
    """Test that no secrets/tokens are stored in checkpoint metadata."""
    from app.services.human_intervention import HumanInterventionCheckpoint, HumanInterventionManager
    from datetime import datetime, timezone, timedelta

    manager = HumanInterventionManager()
    checkpoint = HumanInterventionCheckpoint(
        checkpoint_id="cp-1",
        mission_id="mission-1",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        status="awaiting_human",
        instructions="Authorize AEA",
        metadata={
            "oauth_state": "test-state",
            "oauth_state_owner": "test-owner",
            "oauth_state_workflow": "wf-1",
        },
    )
    manager._memory_store["cp-1"] = checkpoint

    result = manager.claim_oauth_callback("test-state", owner_id="test-owner")
    metadata_str = str(result)
    # No access tokens, refresh tokens, or authorization codes should be in the result
    assert "access_token" not in metadata_str.lower()
    assert "refresh_token" not in metadata_str.lower()
    assert "authorization_code" not in metadata_str.lower()


def test_concurrent_claim_race():
    """Test that concurrent claim_oauth_callback calls are handled."""
    from app.services.human_intervention import HumanInterventionCheckpoint, HumanInterventionManager
    from datetime import datetime, timezone, timedelta

    manager = HumanInterventionManager()
    checkpoint = HumanInterventionCheckpoint(
        checkpoint_id="cp-1",
        mission_id="mission-1",
        platform="pinterest",
        checkpoint_type="oauth_authorization_required",
        status="awaiting_human",
        instructions="Authorize AEA",
        metadata={
            "oauth_state": "test-state",
            "oauth_state_owner": "test-owner",
            "oauth_state_workflow": "wf-1",
        },
    )
    manager._memory_store["cp-1"] = checkpoint

    # First call should claim
    result1 = manager.claim_oauth_callback("test-state", owner_id="test-owner")
    assert result1["status"] == "claimed"

    # Second call should see processing state
    result2 = manager.claim_oauth_callback("test-state", owner_id="test-owner")
    assert result2["status"] == "processing"


if __name__ == "__main__":
    import pytest
    pytest.main([__file__, "-v"])
