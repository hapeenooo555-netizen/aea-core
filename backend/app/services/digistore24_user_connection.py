"""Secure per-user Digistore24 connection primitives.

The app's developer key is a backend secret. A user's API key is encrypted
before persistence and is never returned by browser-facing endpoints.
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx

API_BASE = "https://www.digistore24.com/api/call"
logger = logging.getLogger(__name__)
_SAFE_VENDOR_CODE = re.compile(r"^[A-Za-z0-9_.:-]{1,64}$")


class Digistore24ConnectionError(RuntimeError):
    """Safe Digistore24 connection error."""


def _safe_vendor_code(response: httpx.Response) -> str | None:
    """Extract only a constrained vendor error code; never expose response text."""
    try:
        payload = response.json()
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    candidates: list[Any] = [payload.get("code")]
    data = payload.get("data")
    if isinstance(data, dict):
        candidates.append(data.get("code"))
    for candidate in candidates:
        if isinstance(candidate, str) and _SAFE_VENDOR_CODE.fullmatch(candidate):
            return candidate
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return str(candidate)
    return None


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
    except httpx.HTTPStatusError as exc:
        # Log only endpoint name, status, constrained vendor code, and exception
        # class. Never log headers, query params, response text, or API tokens.
        vendor_code = _safe_vendor_code(exc.response)
        logger.warning(
            "Digistore24 API returned HTTP status; function=%s status=%s exception=%s vendor_code=%s",
            function,
            exc.response.status_code,
            type(exc).__name__,
            vendor_code or "unavailable",
        )
        raise Digistore24ConnectionError(
            "Digistore24 API request failed. Please try again."
        ) from exc
    except httpx.HTTPError as exc:
        logger.warning(
            "Digistore24 API transport failure; function=%s exception=%s",
            function,
            type(exc).__name__,
        )
        raise Digistore24ConnectionError(
            "Could not reach Digistore24. Please try again."
        ) from exc

    try:
        payload = response.json()
    except ValueError as exc:
        logger.warning(
            "Digistore24 API returned invalid JSON; function=%s status=%s",
            function,
            response.status_code,
        )
        raise Digistore24ConnectionError(
            "Digistore24 returned an invalid response. Please try again."
        ) from exc

    if not isinstance(payload, dict) or str(payload.get("result", "")).lower() != "success":
        # Do not forward vendor/API error text that may contain sensitive details.
        logger.warning(
            "Digistore24 API did not confirm success; function=%s status=%s",
            function,
            response.status_code,
        )
        raise Digistore24ConnectionError("Digistore24 did not confirm the connection request.")
    return payload


def request_user_api_key(
    return_url: str,
    *,
    site_url: str,
    cancel_url: str,
    comment: str = "AEA Affiliate Hub",
) -> dict[str, str]:
    # Digistore24's interactive key request also receives the public app URL,
    # cancellation destination, and a vendor-readable application comment.
    for label, value in (("callback", return_url), ("site", site_url), ("cancel", cancel_url)):
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise Digistore24ConnectionError(
                f"The Digistore24 {label} URL must be an HTTPS URL."
            )
    payload = _call(
        "requestApiKey",
        {
            "permissions": "writable",
            "return_url": return_url,
            "site_url": site_url,
            "cancel_url": cancel_url,
            "comment": comment,
        },
    )
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


def load_connected_api_key(user_id: str) -> str:
    """Load and decrypt the Digistore24 credential for the authenticated user."""
    if not user_id:
        raise Digistore24ConnectionError("Authenticated user is required.")
    from app import database

    client = database.get_supabase_oauth_callback_client()
    if client is None:
        raise Digistore24ConnectionError("Secure Digistore24 storage is not configured.")
    try:
        result = (
            client.table("digistore24_user_connections")
            .select("api_key_ciphertext")
            .eq("user_id", user_id)
            .limit(1)
            .execute()
        )
        rows = getattr(result, "data", None) or []
    except Exception as exc:
        raise Digistore24ConnectionError("Could not load the connected Digistore24 account.") from exc
    ciphertext = rows[0].get("api_key_ciphertext") if rows else None
    if not isinstance(ciphertext, str) or not ciphertext:
        raise Digistore24ConnectionError("Connect your Digistore24 account before marketplace research.")
    return decrypt_api_key(ciphertext)
