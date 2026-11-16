"""ClickPesa webhook checksum validation.

Implements the provider's canonical JSON + HMAC-SHA256 checksum contract.
The caller must still reconcile the event against the provider's payment query
and the immutable local order before granting entitlements.
"""
from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any


def _canonicalize(value: Any) -> Any:
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    if isinstance(value, dict):
        return {key: _canonicalize(value[key]) for key in sorted(value)}
    return value


def create_payload_checksum(secret: str, payload: dict[str, Any]) -> str:
    if not secret:
        raise ValueError("ClickPesa checksum secret is not configured.")
    unsigned = {
        key: value
        for key, value in payload.items()
        if key not in {"checksum", "checksumMethod"}
    }
    serialized = json.dumps(
        _canonicalize(unsigned),
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hmac.new(
        secret.encode("utf-8"),
        serialized.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def verify_payload_checksum(
    secret: str,
    payload: dict[str, Any],
) -> bool:
    received = payload.get("checksum")
    if not isinstance(received, str) or len(received) != 64:
        return False
    try:
        expected = create_payload_checksum(secret, payload)
    except (TypeError, ValueError):
        return False
    return hmac.compare_digest(expected.lower(), received.lower())
