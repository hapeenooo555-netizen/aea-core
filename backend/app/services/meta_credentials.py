"""Owner-scoped Meta credential retrieval and verified asset selection."""
from __future__ import annotations
import json
import os
import time
from typing import Any
from cryptography.fernet import Fernet, InvalidToken
from supabase import create_client


def load_meta_credentials(owner_id: str) -> dict[str, Any] | None:
    if not owner_id:
        return None
    url = (os.getenv("SUPABASE_URL") or "").strip()
    service_key = (os.getenv("SUPABASE_SERVICE_ROLE_KEY") or "").strip()
    encryption_key = (os.getenv("META_TOKEN_ENCRYPTION_KEY") or "").strip()
    if not url or not service_key or not encryption_key:
        return None
    try:
        response = create_client(url, service_key).table("meta_oauth_credentials").select("encrypted_credentials,scopes").eq("owner_id", owner_id).limit(1).execute()
        rows = response.data or []
        if not rows:
            return None
        raw = Fernet(encryption_key.encode()).decrypt(rows[0]["encrypted_credentials"].encode())
        bundle = json.loads(raw.decode())
        if not isinstance(bundle, dict) or not isinstance(bundle.get("access_token"), str):
            return None
        expires_in = bundle.get("expires_in")
        issued_at = bundle.get("issued_at")
        if expires_in and issued_at and time.time() >= int(issued_at) + int(expires_in):
            return None
        bundle["_verified_scopes"] = rows[0].get("scopes") or []
        return bundle
    except (InvalidToken, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
    except Exception:
        return None


def resolve_meta_asset(credentials: dict[str, Any], channel: str, requested_asset_id: str | None = None) -> dict[str, str] | None:
    if channel not in {"facebook", "instagram"}:
        return None
    pages = credentials.get("pages")
    if not isinstance(pages, list):
        return None
    candidates: list[dict[str, str]] = []
    for page in pages:
        if not isinstance(page, dict) or not page.get("id") or not page.get("access_token"):
            continue
        if channel == "facebook":
            candidates.append({"asset_id": str(page["id"]), "access_token": str(page["access_token"]), "page_id": str(page["id"])})
        else:
            ig = page.get("instagram_business_account")
            if isinstance(ig, dict) and ig.get("id"):
                candidates.append({"asset_id": str(ig["id"]), "access_token": str(page["access_token"]), "page_id": str(page["id"])})
    if requested_asset_id:
        candidates = [item for item in candidates if item["asset_id"] == str(requested_asset_id)]
    return candidates[0] if len(candidates) == 1 else None
