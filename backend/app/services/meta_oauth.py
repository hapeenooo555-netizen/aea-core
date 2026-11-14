"""Meta OAuth orchestration for Facebook Login and WhatsApp Embedded Signup."""
from __future__ import annotations
import os
from urllib.parse import urlencode
from datetime import datetime, timedelta, timezone
import httpx
from app.services import meta_user_connection as vault

class MetaOAuthError(RuntimeError):
    pass

def _required(name: str) -> str:
    value=os.getenv(name,"").strip()
    if not value: raise MetaOAuthError(f"Meta server configuration is missing: {name}")
    return value

def graph_version() -> str:
    value=os.getenv("META_GRAPH_API_VERSION","v23.0").strip()
    if not value.startswith("v") or not value[1:].replace(".","").isdigit():
        raise MetaOAuthError("Meta Graph API version is invalid.")
    return value

def start_flow(owner_id: str, purpose: str) -> dict:
    app_id=_required("META_APP_ID")
    redirect_uri=_required("META_OAUTH_REDIRECT_URI")
    if not redirect_uri.startswith("https://"): raise MetaOAuthError("Meta OAuth redirect URI must use HTTPS.")
    config_env={"facebook_login":"META_FACEBOOK_LOGIN_CONFIG_ID","whatsapp_embedded_signup":"META_WHATSAPP_EMBEDDED_SIGNUP_CONFIG_ID"}
    if purpose not in config_env: raise MetaOAuthError("Unsupported Meta authorization flow.")
    config_id=_required(config_env[purpose])
    state=vault.create_oauth_state(owner_id,purpose)
    result={"purpose":purpose,"state":state,"app_id":app_id,"config_id":config_id,"redirect_uri":redirect_uri,"graph_api_version":graph_version()}
    if purpose=="facebook_login":
        params=urlencode({"client_id":app_id,"redirect_uri":redirect_uri,"state":state,"config_id":config_id,"response_type":"code","override_default_response_type":"true"})
        result["authorization_url"]=f"https://www.facebook.com/{graph_version()}/dialog/oauth?{params}"
    return result

async def complete_flow(state: str, code: str, purpose: str, expected_owner_id: str | None=None) -> dict:
    if not isinstance(code,str) or not code.strip() or len(code)>4096: raise MetaOAuthError("Meta authorization code is invalid.")
    state_owner=vault.consume_oauth_state(state,purpose)
    owner_id=state_owner["owner_id"]
    if expected_owner_id and owner_id!=expected_owner_id: raise MetaOAuthError("Meta OAuth state does not belong to this account.")
    app_id=_required("META_APP_ID"); secret=_required("META_APP_SECRET")
    redirect_uri=_required("META_OAUTH_REDIRECT_URI"); base=f"https://graph.facebook.com/{graph_version()}"
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            token_response=await client.get(f"{base}/oauth/access_token",params={"client_id":app_id,"client_secret":secret,"redirect_uri":redirect_uri,"code":code})
            token_response.raise_for_status(); payload=token_response.json()
            token=payload.get("access_token") if isinstance(payload,dict) else None
            if not isinstance(token,str) or not token: raise MetaOAuthError("Meta did not return an access token.")
            me_response=await client.get(f"{base}/me",params={"fields":"id,name"},headers={"Authorization":f"Bearer {token}"})
            me_response.raise_for_status(); me=me_response.json()
    except MetaOAuthError: raise
    except (httpx.HTTPError,ValueError,TypeError) as exc: raise MetaOAuthError("Meta authorization could not be verified. Restart the connection.") from exc
    expires=payload.get("expires_in"); expires_at=None
    if isinstance(expires,(int,float)) and expires>0: expires_at=(datetime.now(timezone.utc)+timedelta(seconds=int(expires))).isoformat()
    scopes=payload.get("granted_scopes") or payload.get("scope") or []
    if isinstance(scopes,str): scopes=[x.strip() for x in scopes.split(",") if x.strip()]
    if not isinstance(scopes,list): scopes=[]
    vault.store_connection(owner_id,token,token_expires_at=expires_at,granted_scopes=[str(x) for x in scopes])
    return {"connected":True,"meta_user_id":me.get("id") if isinstance(me,dict) else None,"display_name":me.get("name") if isinstance(me,dict) else None,"asset_discovery":"pending","purpose":purpose}
