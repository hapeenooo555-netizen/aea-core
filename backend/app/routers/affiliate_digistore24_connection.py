"""Authenticated Digistore24 account connect/disconnect/status endpoints.

Connection state and API credentials are persisted only through the dedicated
Supabase service client after user identity and callback state are verified.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode, urlparse, urlunparse, parse_qsl

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import RedirectResponse

from app import database
from app.dependencies import get_current_user_id
from app.services.digistore24_user_connection import (
    Digistore24ConnectionError,
    encrypt_api_key,
    hash_state,
    new_state,
    request_user_api_key,
    retrieve_user_api_key,
    state_expiry,
)

router = APIRouter(prefix="/affiliate/digistore24/connection", tags=["digistore24-connection"])


def _service_client():
    client = database.get_supabase_oauth_callback_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Secure Digistore24 storage is not configured.")
    return client


def _callback_url(state: str) -> str:
    value = os.getenv("DIGISTORE24_CALLBACK_URL", "").strip()
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise HTTPException(status_code=503, detail="Digistore24 callback URL is not configured as HTTPS.")
    # Bind the Digistore24 redirect to the same one-time state persisted below.
    query = [(key, item) for key, item in parse_qsl(parsed.query) if key != "state"]
    query.append(("state", state))
    return urlunparse(parsed._replace(query=urlencode(query)))


@router.post("/start")
def start_connection(user_id: str = Depends(get_current_user_id)) -> dict[str, str]:
    client = _service_client()
    state = new_state()
    try:
        initiated = request_user_api_key(_callback_url(state))
        client.table("digistore24_connect_states").insert({
            "state_hash": hash_state(state),
            "user_id": user_id,
            "request_token": initiated["request_token"],
            "expires_at": state_expiry(),
        }).execute()
        return {"authorization_url": initiated["request_url"], "state": state}
    except Digistore24ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not persist Digistore24 connection state.") from exc


@router.get("/callback")
def finish_connection(state: str = Query(..., min_length=20, max_length=256)) -> RedirectResponse:
    client = _service_client()
    try:
        result = client.table("digistore24_connect_states").select(
            "state_hash,user_id,request_token,expires_at,consumed_at"
        ).eq("state_hash", hash_state(state)).is_("consumed_at", "null").limit(1).execute()
        records = getattr(result, "data", None) or []
        if not records:
            raise HTTPException(status_code=400, detail="Digistore24 callback state is invalid or already used.")
        record = records[0]
        from datetime import datetime, timezone
        expires = datetime.fromisoformat(record["expires_at"].replace("Z", "+00:00"))
        if expires <= datetime.now(timezone.utc):
            raise HTTPException(status_code=400, detail="Digistore24 connection request expired.")
        # Consume the state before making the retrieve call to prevent replay.
        client.table("digistore24_connect_states").update({
            "consumed_at": datetime.now(timezone.utc).isoformat()
        }).eq("state_hash", record["state_hash"]).is_("consumed_at", "null").execute()
        api_key = retrieve_user_api_key(record["request_token"])
        ciphertext = encrypt_api_key(api_key)
        client.table("digistore24_user_connections").upsert({
            "user_id": record["user_id"],
            "api_key_ciphertext": ciphertext,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }, on_conflict="user_id").execute()
    except HTTPException:
        raise
    except Digistore24ConnectionError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Digistore24 connection could not be saved.") from exc

    hub_url = os.getenv("AEA_HUB_URL", "https://aea-affiliate-hub.lovable.app").rstrip("/")
    return RedirectResponse(f"{hub_url}/settings/integrations?digistore24=connected", status_code=303)


@router.get("/status")
def connection_status(user_id: str = Depends(get_current_user_id)) -> dict[str, bool]:
    client = _service_client()
    try:
        result = client.table("digistore24_user_connections").select("user_id").eq("user_id", user_id).limit(1).execute()
        return {"connected": bool(getattr(result, "data", None))}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not check Digistore24 connection status.") from exc


@router.delete("")
def disconnect(user_id: str = Depends(get_current_user_id)) -> dict[str, bool]:
    client = _service_client()
    try:
        # Credential deletion is performed locally. Digistore24 unregister() needs
        # the user's key and is intentionally not called until its API contract is tested.
        client.table("digistore24_user_connections").delete().eq("user_id", user_id).execute()
        return {"connected": False}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not disconnect Digistore24.") from exc
