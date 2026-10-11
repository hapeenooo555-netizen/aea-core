"""Meta OAuth 2.0 authorization-code flow for HAPE BROTHERS.

Tokens are encrypted at rest and stored in a dedicated table accessible only
through the service-role client. This router never returns token material.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from fastapi.responses import RedirectResponse
from supabase import create_client

from app.dependencies import get_current_user_id
from app.services.stores.platform_connection_store import PlatformConnectionStore

router = APIRouter(prefix="/connectors/meta/oauth", tags=["meta-oauth"])
_STATE_COOKIE = "aea_meta_oauth_state"
_STATE_TTL = 900
_DEFAULT_SCOPES = (
    "pages_show_list,pages_read_engagement,pages_manage_posts,"
    "instagram_basic,instagram_content_publish,"
    "whatsapp_business_management,whatsapp_business_messaging,business_management"
)


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"{name} is not configured")
    return value


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _state_signature(payload: str, secret: str) -> str:
    return _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())


def _make_state(owner_id: str, secret: str, now: int | None = None) -> str:
    payload = _b64(json.dumps({
        "owner_id": owner_id,
        "issued_at": int(now if now is not None else time.time()),
        "nonce": secrets.token_urlsafe(32),
    }, separators=(",", ":"), sort_keys=True).encode())
    return f"{payload}.{_state_signature(payload, secret)}"


def _verify_state(state: str, secret: str, now: int | None = None) -> dict[str, Any] | None:
    try:
        payload, signature = state.split(".", 1)
        if not hmac.compare_digest(signature, _state_signature(payload, secret)):
            return None
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        issued = int(data["issued_at"])
        current = int(now if now is not None else time.time())
        if current < issued or current - issued > _STATE_TTL:
            return None
        if not isinstance(data.get("owner_id"), str) or not data["owner_id"] or not data.get("nonce"):
            return None
        return data
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def _fernet() -> Fernet:
    key = _required_env("META_TOKEN_ENCRYPTION_KEY")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="META_TOKEN_ENCRYPTION_KEY is invalid") from exc


def _credential_client():
    """Dedicated privileged client used only for the isolated OAuth credential table."""
    url = _required_env("SUPABASE_URL")
    service_key = _required_env("SUPABASE_SERVICE_ROLE_KEY")
    return create_client(url, service_key)


def _persist_credentials(owner_id: str, token_data: dict[str, Any], profile: dict[str, Any], pages: list[dict[str, Any]]) -> None:
    access_token = token_data.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise HTTPException(status_code=502, detail="Meta did not return an access token")
    encrypted = _fernet().encrypt(json.dumps({
        "access_token": access_token,
        "token_type": token_data.get("token_type", "bearer"),
        "expires_in": token_data.get("expires_in"),
        "issued_at": int(time.time()),
        "pages": pages,
    }, separators=(",", ":")).encode()).decode("ascii")
    client = _credential_client()
    row = {
        "owner_id": owner_id,
        "encrypted_credentials": encrypted,
        "meta_user_id": str(profile.get("id") or ""),
        "display_name": profile.get("name"),
        "scopes": token_data.get("scope", "").split(",") if isinstance(token_data.get("scope"), str) else [],
        "expires_at": None,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    # Use an upsert keyed by owner_id; the DB constraint is added by the
    # accompanying migration. The encrypted blob is never returned to clients.
    client.table("meta_oauth_credentials").upsert(row, on_conflict="owner_id").execute()


@router.get("/start")
async def start_meta_oauth(
    response: Response,
    current_user_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Return Meta's OAuth authorization URL and set an HttpOnly state cookie."""
    app_id = _required_env("META_APP_ID")
    redirect_uri = _required_env("META_REDIRECT_URI")
    secret = _required_env("META_OAUTH_STATE_SECRET")
    state = _make_state(current_user_id, secret)
    scopes = (os.getenv("META_OAUTH_SCOPES") or _DEFAULT_SCOPES).strip()
    query = urlencode({
        "client_id": app_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "response_type": "code",
        "scope": scopes,
    })
    response.set_cookie(
        _STATE_COOKIE, state, max_age=_STATE_TTL, httponly=True,
        secure=True, samesite="lax", path="/connectors/meta/oauth",
    )
    return {
        "success": True,
        "status": "awaiting_authorization",
        "authorization_url": f"https://www.facebook.com/{os.getenv('META_GRAPH_API_VERSION', 'v23.0')}/dialog/oauth?{query}",
        "expires_in": _STATE_TTL,
        "message": "Open the authorization URL to grant the requested Meta permissions.",
    }


@router.get("/callback")
async def meta_oauth_callback(
    request: Request,
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
) -> dict[str, Any]:
    """Validate state, exchange the code, verify identity/assets, and store encrypted credentials."""
    if error:
        raise HTTPException(status_code=400, detail={"error": error, "description": error_description or "Authorization was denied"})
    if not code or not state:
        raise HTTPException(status_code=400, detail="Missing OAuth code or state")
    cookie_state = request.cookies.get(_STATE_COOKIE, "")
    if not cookie_state or not hmac.compare_digest(cookie_state, state):
        raise HTTPException(status_code=400, detail="OAuth state cookie mismatch")
    secret = _required_env("META_OAUTH_STATE_SECRET")
    state_data = _verify_state(state, secret)
    if not state_data:
        raise HTTPException(status_code=400, detail="OAuth state is invalid or expired")

    app_id = _required_env("META_APP_ID")
    app_secret = _required_env("META_APP_SECRET")
    redirect_uri = _required_env("META_REDIRECT_URI")
    graph_base = os.getenv("META_GRAPH_BASE_URL", "https://graph.facebook.com").rstrip("/")
    version = os.getenv("META_GRAPH_API_VERSION", "v23.0")
    base = f"{graph_base}/{version}"

    try:
        with httpx.Client(timeout=20.0) as client:
            token_response = client.get(
                f"{base}/oauth/access_token",
                params={"client_id": app_id, "client_secret": app_secret, "redirect_uri": redirect_uri, "code": code},
            )
            token_response.raise_for_status()
            token_data = token_response.json()
            access_token = token_data.get("access_token")
            if not access_token:
                raise HTTPException(status_code=502, detail="Meta token exchange returned no access token")
            headers = {"Authorization": f"Bearer {access_token}"}
            profile_response = client.get(f"{base}/me", params={"fields": "id,name"}, headers=headers)
            profile_response.raise_for_status()
            profile = profile_response.json()
            permissions_response = client.get(f"{base}/me/permissions", headers=headers)
            permissions_response.raise_for_status()
            permission_rows = permissions_response.json().get("data") or []
            granted_scopes = [
                str(item.get("permission"))
                for item in permission_rows
                if isinstance(item, dict) and item.get("status") == "granted" and item.get("permission")
            ]
            if "pages_show_list" not in granted_scopes:
                raise HTTPException(status_code=403, detail="Meta authorization did not grant pages_show_list")
            token_data["scope"] = ",".join(granted_scopes)
            pages_response = client.get(
                f"{base}/me/accounts",
                params={"fields": "id,name,access_token,instagram_business_account{id,username}"},
                headers=headers,
            )
            # Page listing can be denied when the app lacks required review/scopes.
            # Fail closed rather than marking an account connected without asset proof.
            pages_response.raise_for_status()
            pages_data = pages_response.json()
            pages = pages_data.get("data") or []
            if not pages:
                raise HTTPException(status_code=403, detail="No Facebook Pages were returned; verify Page access and granted permissions")
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail="Meta OAuth or asset verification failed") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach Meta OAuth endpoints") from exc

    # Persist only after OAuth exchange, identity, and Page access have succeeded.
    _persist_credentials(state_data["owner_id"], token_data, profile, pages)
    result = {
        "success": True,
        "status": "connected",
        "platform": "meta",
        "meta_user_id": str(profile.get("id") or ""),
        "display_name": profile.get("name"),
        "pages_verified": len(pages),
        "instagram_assets_verified": sum(1 for page in pages if page.get("instagram_business_account")),
        "token_returned_to_client": False,
        "message": "Meta authorization and Page access were verified. Publishing remains gated until a selected asset and permissions are validated for the requested action.",
    }
    return result


@router.post("/disconnect")
async def disconnect_meta_oauth(current_user_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """Remove the current owner's encrypted Meta credentials."""
    client = _credential_client()
    client.table("meta_oauth_credentials").delete().eq("owner_id", current_user_id).execute()
    return {"success": True, "status": "disconnected", "owner_id": current_user_id}
