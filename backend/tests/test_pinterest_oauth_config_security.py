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
