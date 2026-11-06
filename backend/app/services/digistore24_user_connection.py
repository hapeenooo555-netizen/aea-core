"""Secure per-user Digistore24 connection primitives.

The app's developer key is a backend secret. A user's API key is encrypted
before persistence and is never returned by browser-facing endpoints.
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

API_BASE = "https://www.digistore24.com/api/call"


class Digistore24ConnectionError(RuntimeError):
    """Safe Digistore24 connection error."""


def _developer_key() -> str:
    key = os.getenv("DIGISTORE24_DEVELOPER_API_KEY", "").strip()
    if not key:
        raise Digistore24ConnectionError("Digistore24 developer API key is not configured.")
    return key


def _fernet():
    secret = os.getenv("DIGISTORE24_CREDENTIAL_ENCRYPTION_KEY", "").strip()
    if not secret:
        raise Digistore24ConnectionError("Digistore24 credential encryption is not configured.")
    try:
        from cryptography.fernet import Fernet, InvalidToken
        Fernet(secret.encode("ascii"))
        return Fernet(secret.encode("ascii"))
    except Exception as exc:
        raise Digistore24ConnectionError("Digistore24 credential encryption key is invalid.") from exc


def encrypt_api_key(api_key: str) -> str:
    if not api_key.strip():
        raise Digistore24ConnectionError("Digistore24 returned an empty API key.")
    return _fernet().encrypt(api_key.encode("utf-8")).decode("ascii")


def decrypt_api_key(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except Exception as exc:
        raise Digistore24ConnectionError("Stored Digistore24 credential cannot be decrypted.") from exc


def hash_state(state: str) -> str:
    return hashlib.sha256(state.encode("utf-8")).hexdigest()


def new_state() -> str:
    return secrets.token_urlsafe(32)


def _call(function: str, params: dict[str, Any]) -> dict[str, Any]:
    try:
        response = httpx.get(
            f"{API_BASE}/{function}",
            params=params,
            headers={"X-DS-API-KEY": _developer_key(), "Accept": "application/json"},
            timeout=15.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        raise Digistore24ConnectionError("Digistore24 API request failed. Please try again.") from exc
    if not isinstance(payload, dict) or str(payload.get("result", "")).lower() != "success":
        # Do not forward vendor/API error text that may contain sensitive details.
        raise Digistore24ConnectionError("Digistore24 did not confirm the connection request.")
    return payload


def request_user_api_key(return_url: str) -> dict[str, str]:
    parsed = urlparse(return_url)
    if parsed.scheme != "https" or not parsed.netloc:
        raise Digistore24ConnectionError("The Digistore24 callback must be an HTTPS URL.")
    payload = _call("requestApiKey", {"permissions": "writable", "return_url": return_url})
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    token, request_url = data.get("request_token"), data.get("request_url")
    if not isinstance(token, str) or not token or not isinstance(request_url, str):
        raise Digistore24ConnectionError("Digistore24 returned an unexpected connection response.")
    destination = urlparse(request_url)
    if destination.scheme != "https" or destination.hostname not in {"digistore24.com", "www.digistore24.com", "digistore24-app.com", "www.digistore24-app.com"}:
        raise Digistore24ConnectionError("Digistore24 returned an unexpected authorization URL.")
    return {"request_token": token, "request_url": request_url}


def retrieve_user_api_key(request_token: str) -> str:
    if not request_token.strip() or len(request_token) > 512:
        raise Digistore24ConnectionError("Digistore24 connection token is invalid.")
    payload = _call("retrieveApiKey", {"token": request_token})
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    api_key = data.get("api_key")
    if not isinstance(api_key, str) or not api_key.strip():
        raise Digistore24ConnectionError("Digistore24 did not return the user's API key.")
    return api_key.strip()


def state_expiry() -> str:
    return (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
