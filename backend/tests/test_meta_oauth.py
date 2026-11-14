from datetime import datetime,timezone
import pytest
from app.services import meta_oauth
from app.services.meta_oauth import MetaOAuthError

def test_start_facebook_login_builds_configured_url(monkeypatch):
    monkeypatch.setenv("META_APP_ID","app-123");monkeypatch.setenv("META_OAUTH_REDIRECT_URI","https://api.example.com/meta/oauth/facebook/callback");monkeypatch.setenv("META_FACEBOOK_LOGIN_CONFIG_ID","cfg-1")
    monkeypatch.setattr(meta_oauth.vault,"create_oauth_state",lambda owner,purpose:"s"*43)
    result=meta_oauth.start_flow("owner-1","facebook_login")
    assert "facebook.com/" in result["authorization_url"]
    assert "state=" in result["authorization_url"]
    assert result["config_id"]=="cfg-1"

def test_start_requires_config_and_fails_closed(monkeypatch):
    monkeypatch.delenv("META_APP_ID",raising=False)
    with pytest.raises(MetaOAuthError):meta_oauth.start_flow("owner-1","facebook_login")

def test_start_whatsapp_embedded_signup_uses_its_own_config(monkeypatch):
    monkeypatch.setenv("META_APP_ID","app-123");monkeypatch.delenv("META_OAUTH_REDIRECT_URI",raising=False);monkeypatch.setenv("META_WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID","wa-config")
    monkeypatch.setattr(meta_oauth.vault,"create_oauth_state",lambda owner,purpose:"w"*43)
    result=meta_oauth.start_flow("owner-1","whatsapp_embedded_signup")
    assert result["config_id"]=="wa-config"
    assert "authorization_url" not in result

@pytest.mark.asyncio
async def test_owner_mismatch_rejected_after_state_consumption(monkeypatch):
    monkeypatch.setattr(meta_oauth.vault,"consume_oauth_state",lambda state,purpose,expected_owner_id=None:{"owner_id":"owner-A","purpose":purpose})
    with pytest.raises(MetaOAuthError,match="does not belong"):
        await meta_oauth.complete_flow("s"*43,"code","facebook_login","owner-B")
