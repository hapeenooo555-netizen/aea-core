from app.services.pinterest_oauth import DEFAULT_SCOPES, PinterestOAuthConfig, PinterestOAuthHelper


def test_oauth_requires_client_id_secret_and_redirect_uri(monkeypatch):
    monkeypatch.setenv("PINTEREST_CLIENT_ID", "test-client")
    monkeypatch.setenv("PINTEREST_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.test/connectors/oauth/callback")
    assert PinterestOAuthConfig().is_configured is True

    monkeypatch.delenv("PINTEREST_CLIENT_SECRET")
    config = PinterestOAuthConfig()
    assert config.is_configured is False
    assert config.has_secret is False

    monkeypatch.setenv("PINTEREST_CLIENT_SECRET", "test-secret")
    monkeypatch.delenv("PINTEREST_REDIRECT_URI")
    assert PinterestOAuthConfig().is_configured is False


def test_default_scopes_use_pinterest_pin_write_scope():
    assert "pins:write" in DEFAULT_SCOPES
    assert "pins:create" not in DEFAULT_SCOPES


def test_authorization_url_fails_closed_without_secret(monkeypatch):
    monkeypatch.setenv("PINTEREST_CLIENT_ID", "test-client")
    monkeypatch.delenv("PINTEREST_CLIENT_SECRET", raising=False)
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.test/connectors/oauth/callback")

    helper = PinterestOAuthHelper(PinterestOAuthConfig())
    try:
        helper.build_authorization_url(state="opaque-test-state")
    except ValueError as exc:
        assert "PINTEREST_CLIENT_SECRET" in str(exc)
    else:
        raise AssertionError("OAuth URL construction should fail when the client secret is missing")


def test_generate_authorization_url_fails_closed_when_unconfigured(monkeypatch):
    monkeypatch.delenv("PINTEREST_CLIENT_ID", raising=False)
    monkeypatch.delenv("PINTEREST_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("PINTEREST_REDIRECT_URI", raising=False)
    helper = PinterestOAuthHelper(PinterestOAuthConfig())
    try:
        helper.generate_authorization_url(owner_id="owner-1", workflow_id="workflow-1")
    except ValueError as exc:
        assert "PINTEREST_CLIENT_ID" in str(exc)
    else:
        raise AssertionError("OAuth URL must not be simulated when config is missing")


def test_state_metadata_binds_owner_and_workflow(monkeypatch):
    import time

    monkeypatch.setenv("PINTEREST_CLIENT_ID", "test-client")
    monkeypatch.setenv("PINTEREST_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv("PINTEREST_REDIRECT_URI", "https://example.test/connectors/oauth/callback")
    helper = PinterestOAuthHelper(PinterestOAuthConfig())
    metadata = helper.state_metadata("opaque-state", "auth-user-uuid", "workflow-1")
    assert metadata["oauth_state_owner"] == "auth-user-uuid"
    assert metadata["oauth_state_workflow"] == "workflow-1"
    assert metadata["oauth_state_expires_at"] > time.time()
