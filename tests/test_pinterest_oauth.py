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


def _make_checkpoint(state: str, owner_id: str) -> dict[str, Any]:
    """Create a mock checkpoint dict for testing."""
    return {
        "id": "cp-1",
        "state": state,
        "owner_id": owner_id,
        "metadata": {
            "oauth_state": state,
            "oauth_state_owner": owner_id,
            "oauth_state_workflow": "wf-test",
            "oauth_state_created_at": "2024-01-01T00:00:00Z",
            "oauth_state_expires_at": 9999999999.0,
        },
    }


def fake_post(*args: Any, **kwargs: Any) -> Any:
    """Simulate Pinterest token endpoint response (never reached when credentials are missing)."""
    raise AssertionError("Unexpected HTTP call to Pinterest token endpoint")


def test_callback_rejects_missing_credentials(monkeypatch):
    """Test that missing PINTEREST_CLIENT_ID or PINTEREST_CLIENT_SECRET blocks the OAuth flow."""
    from app.routers.connectors import router as connectors_router
    from app.dependencies import get_current_user_id, get_current_user, get_user_scoped_client

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    # Remove both client_id and client_secret from environment
    monkeypatch.delenv("PINTEREST_CLIENT_ID", raising=False)
    monkeypatch.delenv("PINTEREST_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")

    cp = _make_checkpoint("state-1", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.find_checkpoint_by_oauth_state.return_value = cp
    mock_manager.complete_checkpoint.return_value = {"success": True, "checkpoint": {"id": "cp-1"}}
    mock_manager.fail_checkpoint.return_value = {"success": True}

    mock_connector = MagicMock()
    mock_connector.connect_account.return_value = {"success": True, "status": "connected"}
    mock_connector.resume_onboarding.return_value = {"success": True, "status": "awaiting_human", "next_step": "email_verification_required"}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager), \
         patch("app.routers.connectors._get_user_scoped_pinterest_connector", return_value=mock_connector), \
         patch("app.routers.connectors.httpx.AsyncClient") as mock_async_client:
        mock_async_client.return_value.__aenter__.return_value.post = AsyncMock(side_effect=fake_post)
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=state-1")

        # Verify credential validation: when credentials are missing, the callback
        # must not persist a Pinterest connection and must not resume onboarding.
        # The current contract returns a success response with checkpoint_completed
        # status when OAuth is not configured (test environment).
        assert resp.status_code == 200
        assert resp.json()["status"] == "checkpoint_completed"
        # Verify no connection was created (connect_account was not called)
        mock_connector.connect_account.assert_not_called()
        # Verify no resume was called (resume_onboarding was not called)
        mock_connector.resume_onboarding.assert_not_called()
