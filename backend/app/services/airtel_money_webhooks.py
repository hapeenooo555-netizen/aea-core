"""Airtel Money callback HMAC primitives.

Airtel documents HMAC-SHA256 with Base64 output for authenticated callbacks.
The portal page supplied so far does not define the exact byte serialization
of the callback body to be signed. This module therefore verifies caller-
supplied signing bytes and deliberately does not invent JSON canonicalization.
"""
from __future__ import annotations

import base64
import hashlib
import hmac


class AirtelCallbackVerificationError(ValueError):
    """Callback signature is missing or invalid."""


def create_callback_hash(private_key: str, message: bytes) -> str:
    """Create Airtel's documented Base64(HMAC-SHA256(private_key, message))."""
    if not isinstance(private_key, str) or not private_key:
        raise ValueError("Airtel callback private key is not configured.")
    if not isinstance(message, bytes):
        raise TypeError("Airtel callback signing message must be bytes.")
    digest = hmac.new(
        private_key.encode("utf-8"),
        message,
        hashlib.sha256,
    ).digest()
    return base64.b64encode(digest).decode("ascii")


def verify_callback_hash(
    private_key: str,
    message: bytes,
    supplied_hash: str,
) -> bool:
    """Constant-time comparison of the Base64 HMAC supplied by Airtel."""
    if not isinstance(supplied_hash, str) or not supplied_hash:
        return False
    try:
        expected = create_callback_hash(private_key, message)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected, supplied_hash)


__all__ = [
    "AirtelCallbackVerificationError",
    "create_callback_hash",
    "verify_callback_hash",
]
