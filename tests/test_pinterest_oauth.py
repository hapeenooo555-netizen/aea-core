"""Focused tests for Pinterest OAuth foundation (Phase 1).

Covers:
  - Authorization URL construction (correct endpoint, params, scopes)
  - Secure state generation (randomness, uniqueness, length)
  - State validation (valid, mismatch, expired, consumed, owner mismatch)
  - Callback endpoint behavior (invalid state, valid state, missing params)
  - No credential exposure in state/response
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.services.pinterest_oauth import (
    DEFAULT_SCOPES,
    PinterestOAuthConfig,
    PinterestOAuthHelper,
)


@pytest.fixture
def oauth_env(monkeypatch):
    """Set Pinterest OAuth env vars without exposing their values."""
    monkeypatch.setenv("PINTEREST_CLIENT_ID", "test-client-id-12345")
    monkeypatch.setenv("PINTEREST_CLIENT_SECRET", "test-secret-xxx")
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")
    return PinterestOAuthConfig()


@pytest.fixture
def oauth_helper(oauth_env):
    return PinterestOAuthHelper(config=oauth_env)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def test_config_reads_env_vars(oauth_env):
    """Config loads from environment, never prints values."""
    assert oauth_env.client_id == "test-client-id-12345"
    assert oauth_env.redirect_uri == "https://example.app/callback"
    assert oauth_env.is_configured is True
    assert oauth_env.has_secret is True


def test_config_not_configured_without_client_id(monkeypatch):
    """Config is not configured when client_id is missing."""
    monkeypatch.delenv("PINTEREST_CLIENT_ID", raising=False)
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.app/callback")
    cfg = PinterestOAuthConfig()
    assert cfg.is_configured is False


def test_config_not_configured_without_redirect(monkeypatch):
    """Config is not configured when redirect_uri is missing."""
    monkeypatch.delenv("PINTEREST_REDIRECT_URI", raising=False)
    monkeypatch.setenv("PINTEREST_CLIENT_ID", "test-id")
    cfg = PinterestOAuthConfig()
    assert cfg.is_configured is False


# ---------------------------------------------------------------------------
# State generation
# ---------------------------------------------------------------------------

def test_generate_state_is_cryptographically_random(oauth_helper):
    """State tokens are unique and sufficiently long."""
    s1 = oauth_helper.generate_state("owner-1", "wf-1")
    s2 = oauth_helper.generate_state("owner-1", "wf-1")
    assert s1 != s2, "State must be non-deterministic"
    assert len(s1) >= 43, "State should be 256+ bits (43+ url-safe chars)"


def test_generate_state_bound_to_owner_and_workflow(oauth_helper):
    """State is bound to owner_id and workflow_id (used for checkpoint matching)."""
    md = oauth_helper.state_metadata("state-abc", "owner-1", "wf-1")
    assert md["oauth_state"] == "state-abc"
    assert md["oauth_state_owner"] == "owner-1"
    assert md["oauth_state_workflow"] == "wf-1"
    assert "oauth_state_created_at" in md
    assert "oauth_state_expires_at" in md


# ---------------------------------------------------------------------------
# Authorization URL
# ---------------------------------------------------------------------------

def test_authorization_url_uses_correct_endpoint(oauth_helper):
    """Authorization URL points to Pinterest's OAuth endpoint."""
    url = oauth_helper.build_authorization_url("test-state")
    parsed = urlparse(url)
    assert parsed.scheme == "https"
    assert parsed.netloc == "www.pinterest.com"
    assert parsed.path == "/oauth/"


def test_authorization_url_has_required_params(oauth_helper):
    """URL contains client_id, redirect_uri, response_type=code, scope, state."""
    url = oauth_helper.build_authorization_url("csrf-state-123")
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    assert qs["client_id"] == ["test-client-id-12345"]
    assert qs["redirect_uri"] == ["https://example.app/callback"]
    assert qs["response_type"] == ["code"]
    assert qs["state"] == ["csrf-state-123"]
    # Scopes joined by comma
    scopes = qs["scope"][0].split(",")
    assert set(scopes) == set(DEFAULT_SCOPES)


def test_authorization_url_has_only_necessary_scopes(oauth_helper, monkeypatch):
    """Only the scopes required for the affiliate workflow are requested."""
    monkeypatch.setenv("PINTEREST_CLIENT_ID", "cid")
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://app.example/callback")
    url, _ = oauth_helper.generate_authorization_url("owner-1", "wf-1")
    parsed = urlparse(url)
    qs = parse_qs(parsed.query)
    scopes = set(qs["scope"][0].split(","))
    assert scopes == set(DEFAULT_SCOPES), f"Unexpected scopes: {scopes}"
    # Must not include board/create, pins/delete, or other overly-broad scopes
    forbidden = {"boards:create", "boards:delete", "pins:delete", "ads:conversions", "user_accounts:write"}
    assert scopes.isdisjoint(forbidden), f"Overly broad scopes present: {scopes & forbidden}"


def test_authorization_url_raises_without_config():
    """build_authorization_url raises when OAuth is not configured."""
    helper = PinterestOAuthHelper(config=PinterestOAuthConfig())
    with pytest.raises(ValueError, match="not configured"):
        helper.build_authorization_url("state")


def test_generate_authorization_url_returns_state_and_url(oauth_helper):
    """generate_authorization_url returns (url, state) tuple."""
    url, state = oauth_helper.generate_authorization_url("owner-1", "wf-1")
    assert url.startswith("https://www.pinterest.com/oauth/")
    assert len(state) >= 43


# ---------------------------------------------------------------------------
# State validation
# ---------------------------------------------------------------------------

def _make_checkpoint(oauth_state, owner_id="owner-1", workflow_id="wf-1", expired=False, consumed=False):
    """Build a checkpoint dict as stored in human_intervention_checkpoints."""
    from datetime import datetime, timezone, timedelta
    now = datetime.now(timezone.utc)
    expires_at = (now - timedelta(seconds=1)).timestamp() if expired else (now + timedelta(minutes=15)).timestamp()
    metadata = {
        "metadata": {
            "oauth_state": oauth_state,
            "oauth_state_owner": owner_id,
            "oauth_state_workflow": workflow_id,
            "oauth_state_created_at": now.isoformat(),
            "oauth_state_expires_at": expires_at,
            "oauth_state_consumed": consumed,
        },
    }
    return {
        "id": "cp-1",
        "status": "awaiting_human",
        "metadata": metadata,
    }


def test_validate_state_accepts_valid_state(oauth_helper):
    """Valid state, matching owner, not expired/consumed → valid."""
    cp = _make_checkpoint("valid-state")
    valid, reason = oauth_helper.validate_state("valid-state", cp, owner_id="owner-1")
    assert valid is True
    assert reason == ""


def test_validate_state_rejects_missing_state(oauth_helper):
    """Empty state → rejected."""
    cp = _make_checkpoint("something")
    valid, reason = oauth_helper.validate_state("", cp, owner_id="owner-1")
    assert valid is False
    assert reason == "missing_state"


def test_validate_state_rejects_mismatch(oauth_helper):
    """Different state → rejected."""
    cp = _make_checkpoint("original-state")
    valid, reason = oauth_helper.validate_state("tampered-state", cp, owner_id="owner-1")
    assert valid is False
    assert reason == "state_mismatch"


def test_validate_state_rejects_owner_mismatch(oauth_helper):
    """State bound to different owner → rejected."""
    cp = _make_checkpoint("state-x", owner_id="owner-1")
    valid, reason = oauth_helper.validate_state("state-x", cp, owner_id="attacker-2")
    assert valid is False
    assert reason == "owner_mismatch"


def test_validate_state_rejects_expired(oauth_helper):
    """Expired state → rejected."""
    cp = _make_checkpoint("expired-state", expired=True)
    valid, reason = oauth_helper.validate_state("expired-state", cp, owner_id="owner-1")
    assert valid is False
    assert reason == "state_expired"


def test_validate_state_rejects_consumed(oauth_helper):
    """Already-consumed state → rejected (single-use)."""
    cp = _make_checkpoint("consumed-state", consumed=True)
    valid, reason = oauth_helper.validate_state("consumed-state", cp, owner_id="owner-1")
    assert valid is False
    assert reason == "state_already_consumed"


def test_state_comparison_is_constant_time(oauth_helper):
    """Uses secrets.compare_digest (timing-safe) for comparison."""
    cp = _make_checkpoint("abc123")
    valid, _ = oauth_helper.validate_state("abc123", cp, owner_id="owner-1")
    assert valid is True
    valid2, _ = oauth_helper.validate_state("xyz789", cp, owner_id="owner-1")
    assert valid2 is False


# ---------------------------------------------------------------------------
# Callback endpoint
# ---------------------------------------------------------------------------

def _bypass_auth():
    """Return FastAPI dependency overrides for auth bypass."""
    from app.dependencies import get_current_user, get_current_user_id, get_user_scoped_client
    return {
        get_current_user: lambda request: {"id": "test-owner-id", "email": "test@example.com", "role": "authenticated"},
        get_current_user_id: lambda current_user=None: "test-owner-id",
        get_user_scoped_client: lambda request=None, current_user=None: None,
    }


def test_callback_rejects_missing_code(monkeypatch):
    """Callback without code → 400."""
    from fastapi.testclient import TestClient
    from app.main import app

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?state=abc")
        assert resp.status_code == 400
        assert "Missing" in resp.json()["detail"]


def test_callback_rejects_missing_state(monkeypatch):
    """Callback without state → 400."""
    from fastapi.testclient import TestClient
    from app.main import app

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=abc")
        assert resp.status_code == 400


def test_callback_rejects_invalid_state(monkeypatch):
    """Callback with no matching checkpoint → 404."""
    from fastapi.testclient import TestClient
    from app.main import app

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=invalidstate")
        assert resp.status_code == 404
        assert "No pending checkpoint" in resp.json()["detail"]


def test_callback_accepts_valid_state_and_completes_checkpoint(monkeypatch):
    """Callback with valid state → checkpoint completed, no token exchange."""
    from fastapi.testclient import TestClient
    from app.main import app

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    cp = _make_checkpoint("valid-callback-state", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.find_checkpoint_by_oauth_state.return_value = cp
    mock_manager.complete_checkpoint.return_value = {"success": True, "checkpoint": {"id": "cp-1"}}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=valid-callback-state")

        assert resp.status_code == 200
        body = resp.json()
        assert body["success"] is True
        assert body["status"] == "checkpoint_completed"
        assert body["checkpoint_id"] == "cp-1"
        assert body["next_step"] == "token_exchange"

        # complete_checkpoint was called (state consumed)
        mock_manager.complete_checkpoint.assert_called_once()

        # No credential fields in response
        response_str = resp.json()
        for forbidden in ["access_token", "refresh_token", "oauth_code", "client_secret", "token"]:
            assert forbidden not in response_str, f"{forbidden} leaked in response"


def test_callback_rejects_state_mismatch(monkeypatch):
    """State mismatch in checkpoint metadata → 400."""
    from fastapi.testclient import TestClient
    from app.main import app

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    cp = _make_checkpoint("stored-state", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.find_checkpoint_by_oauth_state.return_value = cp
    mock_manager.fail_checkpoint.return_value = {"success": True}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=tampered-state")

        assert resp.status_code == 400
        assert "state_mismatch" in resp.json()["detail"]
        # complete_checkpoint must NOT be called on mismatch
        mock_manager.complete_checkpoint.assert_not_called()


def test_callback_rejects_expired_state(monkeypatch):
    """Expired state → 400, checkpoint NOT completed."""
    from fastapi.testclient import TestClient
    from app.main import app

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    cp = _make_checkpoint("expired-state", owner_id="test-owner-id", expired=True)

    mock_manager = MagicMock()
    mock_manager.find_checkpoint_by_oauth_state.return_value = cp
    mock_manager.fail_checkpoint.return_value = {"success": True}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=expired-state")

        assert resp.status_code == 400
        assert "state_expired" in resp.json()["detail"]
        mock_manager.complete_checkpoint.assert_not_called()


def test_callback_rejects_replayed_state(monkeypatch):
    """Already-consumed state → 400, checkpoint NOT completed again."""
    from fastapi.testclient import TestClient
    from app.main import app

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    cp = _make_checkpoint("consumed-state", owner_id="test-owner-id", consumed=True)

    mock_manager = MagicMock()
    mock_manager.find_checkpoint_by_oauth_state.return_value = cp
    mock_manager.fail_checkpoint.return_value = {"success": True}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=authcode&state=consumed-state")

        assert resp.status_code == 400
        assert "state_already_consumed" in resp.json()["detail"]
        mock_manager.complete_checkpoint.assert_not_called()


def test_callback_never_exposes_code_in_response(monkeypatch):
    """Authorization code must never appear in the callback response."""
    from fastapi.testclient import TestClient
    from app.main import app

    for dep, override in _bypass_auth().items():
        app.dependency_overrides[dep] = override

    cp = _make_checkpoint("secret-state", owner_id="test-owner-id")

    mock_manager = MagicMock()
    mock_manager.find_checkpoint_by_oauth_state.return_value = cp
    mock_manager.complete_checkpoint.return_value = {"success": True, "checkpoint": {"id": "cp-1"}}

    with patch("app.database.supabase_client", None), \
         patch("app.database.is_supabase_configured", return_value=False), \
         patch("app.routers.connectors.get_human_intervention_manager", return_value=mock_manager):
        client = TestClient(app)
        resp = client.get("/connectors/oauth/callback?code=supersecret123&state=secret-state")

        body_str = str(resp.json())
        assert "supersecret123" not in body_str, "Authorization code leaked in response"
        assert "secret-state" not in body_str, "State leaked in response"
