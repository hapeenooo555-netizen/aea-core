"""Server-only, per-user Meta credential vault and OAuth state primitives.

Access tokens are encrypted before persistence. This module never returns
ciphertext or plaintext credentials from browser-facing status operations.
"""
from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken


class MetaCredentialError(RuntimeError):
    """Safe Meta credential storage error."""


def _fernet() -> Fernet:
    key = os.getenv("META_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not key:
        raise MetaCredentialError("Meta credential encryption is not configured.")
    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, UnicodeEncodeError) as exc:
        raise MetaCredentialError("Meta credential encryption key is invalid.") from exc


def encrypt_access_token(access_token: str) -> str:
    if not isinstance(access_token, str) or not access_token.strip():
        raise MetaCredentialError("Meta returned an empty access token.")
    return _fernet().encrypt(access_token.encode("utf-8")).decode("ascii")


def decrypt_access_token(ciphertext: str) -> str:
    if not isinstance(ciphertext, str) or not ciphertext:
        raise MetaCredentialError("Stored Meta credential is unavailable.")
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, UnicodeEncodeError) as exc:
        raise MetaCredentialError("Stored Meta credential cannot be decrypted.") from exc


def new_oauth_state() -> str:
    """Generate a high-entropy, URL-safe, one-time OAuth state value."""
    return secrets.token_urlsafe(32)


def hash_oauth_state(state: str) -> str:
    if not isinstance(state, str) or len(state) < 32 or len(state) > 256:
        raise MetaCredentialError("Meta OAuth state is invalid.")
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def oauth_state_expiry(now: datetime | None = None) -> str:
    issued_at = now or datetime.now(timezone.utc)
    if issued_at.tzinfo is None:
        raise ValueError("OAuth state clock must be timezone-aware.")
    return (issued_at + timedelta(minutes=10)).isoformat()


def _service_client():
    # Delayed import avoids coupling crypto primitives to app initialization.
    from app import database

    client = database.get_supabase_oauth_callback_client()
    if client is None:
        raise MetaCredentialError("Secure Meta storage is not configured.")
    return client


def create_oauth_state(owner_id: str, purpose: str = "facebook_login") -> str:
    if not owner_id:
        raise MetaCredentialError("Authenticated owner is required.")
    if purpose not in {"facebook_login", "whatsapp_embedded_signup"}:
        raise MetaCredentialError("Meta OAuth purpose is unsupported.")
    state = new_oauth_state()
    try:
        _service_client().table("meta_oauth_states").insert({
            "state_hash": hash_oauth_state(state),
            "owner_id": owner_id,
            "purpose": purpose,
            "expires_at": oauth_state_expiry(),
        }).execute()
    except Exception as exc:
        raise MetaCredentialError("Could not securely create Meta OAuth state.") from exc
    return state


def consume_oauth_state(state: str, purpose: str | None = None, expected_owner_id: str | None = None) -> dict[str, str]:
    """Atomically consume an unexpired state; only one callback can win."""
    state_hash = hash_oauth_state(state)
    now = datetime.now(timezone.utc).isoformat()
    client = _service_client()
    try:
        query = (
            client.table("meta_oauth_states")
            .update({"consumed_at": now})
            .eq("state_hash", state_hash)
            .is_("consumed_at", "null")
            .gt("expires_at", now)
        )
        if purpose:
            query = query.eq("purpose", purpose)
        if expected_owner_id:
            query = query.eq("owner_id", expected_owner_id)
        response = query.select("owner_id,purpose,expires_at").execute()
        rows = getattr(response, "data", None) or []
    except Exception as exc:
        raise MetaCredentialError("Could not validate Meta OAuth state.") from exc
    if len(rows) != 1:
        raise MetaCredentialError("Meta OAuth state is invalid, expired, or already used.")
    row = rows[0]
    return {"owner_id": row["owner_id"], "purpose": row["purpose"]}


def store_connection(
    owner_id: str,
    access_token: str,
    *,
    token_expires_at: str | None = None,
    granted_scopes: list[str] | None = None,
    facebook_pages: list[dict[str, Any]] | None = None,
    instagram_accounts: list[dict[str, Any]] | None = None,
    whatsapp_accounts: list[dict[str, Any]] | None = None,
) -> None:
    """Persist encrypted token and discovered non-secret asset metadata."""
    if not owner_id:
        raise MetaCredentialError("Authenticated owner is required.")
    assets = {
        "facebook_pages": facebook_pages or [],
        "instagram_accounts": instagram_accounts or [],
        "whatsapp_accounts": whatsapp_accounts or [],
    }
    try:
        _service_client().table("meta_user_connections").upsert({
            "owner_id": owner_id,
            "encrypted_access_token": encrypt_access_token(access_token),
            "token_expires_at": token_expires_at,
            "granted_scopes": sorted(set(granted_scopes or [])),
            "assets": json.loads(json.dumps(assets)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="owner_id").execute()
    except MetaCredentialError:
        raise
    except Exception as exc:
        raise MetaCredentialError("Could not securely save the Meta connection.") from exc


def load_access_token(owner_id: str) -> str:
    if not owner_id:
        raise MetaCredentialError("Authenticated owner is required.")
    try:
        response = (
            _service_client().table("meta_user_connections")
            .select("encrypted_access_token")
            .eq("owner_id", owner_id)
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None) or []
    except Exception as exc:
        raise MetaCredentialError("Could not load the Meta connection.") from exc
    if len(rows) != 1:
        raise MetaCredentialError("Meta account is not connected.")
    return decrypt_access_token(rows[0].get("encrypted_access_token", ""))


def connection_status(owner_id: str) -> dict[str, Any]:
    """Return safe metadata only; never return token or ciphertext."""
    if not owner_id:
        raise MetaCredentialError("Authenticated owner is required.")
    try:
        response = (
            _service_client().table("meta_user_connections")
            .select("granted_scopes,assets,token_expires_at,updated_at")
            .eq("owner_id", owner_id)
            .limit(1)
            .execute()
        )
        rows = getattr(response, "data", None) or []
    except Exception as exc:
        raise MetaCredentialError("Could not check the Meta connection.") from exc
    if not rows:
        return {"connected": False, "assets": {"facebook_pages": [], "instagram_accounts": [], "whatsapp_accounts": []}}
    row = rows[0]
    return {
        "connected": True,
        "granted_scopes": row.get("granted_scopes") or [],
        "assets": row.get("assets") or {},
        "token_expires_at": row.get("token_expires_at"),
        "updated_at": row.get("updated_at"),
    }


def disconnect_owner(owner_id: str) -> bool:
    """Delete only the authenticated owner's local Meta credentials/assets."""
    if not owner_id:
        raise MetaCredentialError("Authenticated owner is required.")
    try:
        _service_client().table("meta_user_connections").delete().eq("owner_id", owner_id).execute()
        return True
    except Exception as exc:
        raise MetaCredentialError("Could not disconnect the Meta account.") from exc


def update_connection_assets(owner_id: str, assets: dict[str, Any]) -> None:
    """Replace discovered asset metadata without changing encrypted credentials."""
    if not owner_id:
        raise MetaCredentialError("Authenticated owner is required.")
    allowed = {"facebook_pages", "instagram_accounts", "whatsapp_accounts", "business_portfolios"}
    safe_assets = {key: assets.get(key, []) for key in allowed}
    try:
        _service_client().table("meta_user_connections").update({
            "assets": json.loads(json.dumps(safe_assets)),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("owner_id", owner_id).execute()
    except Exception as exc:
        raise MetaCredentialError("Could not save discovered Meta assets.") from exc
