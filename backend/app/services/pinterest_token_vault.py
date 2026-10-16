"""Server-only encrypted storage for Pinterest OAuth credentials."""
from __future__ import annotations

import base64
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

TABLE = "pinterest_oauth_tokens"


def _fernet() -> Fernet:
    configured = os.getenv("PINTEREST_TOKEN_ENCRYPTION_KEY", "").strip()
    if not configured:
        raise RuntimeError("PINTEREST_TOKEN_ENCRYPTION_KEY is not configured")
    # Accept an unpadded URL-safe base64 encoding of 32 random bytes.
    key = configured.encode("ascii")
    key += b"=" * ((4 - len(key) % 4) % 4)
    raw = base64.urlsafe_b64decode(key)
    if len(raw) != 32:
        raise RuntimeError("PINTEREST_TOKEN_ENCRYPTION_KEY must encode 32 random bytes")
    return Fernet(base64.urlsafe_b64encode(raw))


def _service_client() -> Any:
    from .. import database as database_module

    client = database_module.get_supabase_oauth_callback_client()
    if client is None:
        raise RuntimeError("Supabase server-side OAuth client is not configured")
    return client


def save_tokens(
    *,
    owner_id: str,
    access_token: str,
    refresh_token: str | None,
    token_type: str | None,
    expires_in: int | None,
    scopes: list[str] | None,
    external_account_id: str | None,
    display_name: str | None,
) -> None:
    """Encrypt credentials before writing; raw tokens never leave this module."""
    if not owner_id or not access_token:
        raise ValueError("owner_id and access_token are required")
    f = _fernet()
    now = datetime.now(timezone.utc)
    expires_at = (now + timedelta(seconds=max(0, int(expires_in or 0)))).isoformat() if expires_in else None
    payload = {
        "owner_id": owner_id,
        "encrypted_access_token": f.encrypt(access_token.encode()).decode(),
        "encrypted_refresh_token": f.encrypt(refresh_token.encode()).decode() if refresh_token else None,
        "token_type": (token_type or "bearer").lower(),
        "expires_at": expires_at,
        "scopes": [s for s in (scopes or []) if isinstance(s, str)],
        "external_account_id": external_account_id,
        "display_name": display_name,
        "updated_at": now.isoformat(),
    }
    _service_client().table(TABLE).upsert(payload, on_conflict="owner_id").execute()


def get_access_token(*, owner_id: str) -> dict[str, Any] | None:
    """Return a decrypted token to server-side callers only."""
    if not owner_id:
        return None
    response = (
        _service_client().table(TABLE)
        .select("encrypted_access_token,encrypted_refresh_token,token_type,expires_at,scopes,external_account_id,display_name")
        .eq("owner_id", owner_id).limit(1).execute()
    )
    rows = response.data or []
    if not rows:
        return None
    row = rows[0]
    try:
        token = _fernet().decrypt(row["encrypted_access_token"].encode()).decode()
    except (InvalidToken, KeyError, ValueError) as exc:
        raise RuntimeError("Stored Pinterest access token could not be decrypted") from exc
    expires_at = row.get("expires_at")
    if expires_at:
        try:
            expires = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
            if expires <= datetime.now(timezone.utc):
                return {**row, "access_token": token, "expired": True}
        except ValueError:
            pass
    return {**row, "access_token": token, "expired": False}
