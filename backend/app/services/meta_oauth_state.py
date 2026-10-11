"""Meta OAuth state helpers.

State is signed, owner-bound, short-lived and verified in constant time.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any

STATE_TTL_SECONDS = 900


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def make_state(owner_id: str, secret: str, now: int | None = None) -> str:
    payload = _b64(json.dumps({
        "owner_id": owner_id,
        "issued_at": int(now if now is not None else time.time()),
        "nonce": secrets.token_urlsafe(32),
    }, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_state(state: str, secret: str, now: int | None = None) -> dict[str, Any] | None:
    try:
        payload, signature = state.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(signature, expected):
            return None
        padded = payload + "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(padded.encode()))
        issued = int(data["issued_at"])
        current = int(now if now is not None else time.time())
        if current < issued or current - issued > STATE_TTL_SECONDS:
            return None
        if not isinstance(data.get("owner_id"), str) or not data["owner_id"] or not data.get("nonce"):
            return None
        return data
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None
