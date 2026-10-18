"""Regression tests for the Pinterest OAuth publishing scopes."""

from app.services.pinterest_oauth import DEFAULT_SCOPES, PinterestOAuthConfig, PinterestOAuthHelper


def test_oauth_requests_read_and_write_scopes_for_pin_publishing():
    scopes = PinterestOAuthHelper(PinterestOAuthConfig()).scopes

    assert "user_accounts:read" in scopes
    assert "boards:read" in scopes
    assert "boards:write" in scopes
    assert "pins:read" in scopes
    assert "pins:write" in scopes


def test_default_scopes_include_required_publishing_permissions():
    assert set(("boards:read", "boards:write", "pins:read", "pins:write")).issubset(
        set(DEFAULT_SCOPES)
    )
