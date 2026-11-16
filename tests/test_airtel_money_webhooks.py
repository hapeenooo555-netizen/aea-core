from __future__ import annotations

import base64
import hashlib
import hmac

import pytest

from app.services.airtel_money_webhooks import (
    create_callback_hash,
    verify_callback_hash,
)


def test_airtel_callback_hash_uses_hmac_sha256_and_base64():
    private_key = "test-private-key"
    message = b'{"transaction":{"id":"aea-123","status_code":"TS"}}'
    expected = base64.b64encode(
        hmac.new(private_key.encode(), message, hashlib.sha256).digest()
    ).decode()
    assert create_callback_hash(private_key, message) == expected
    assert verify_callback_hash(private_key, message, expected)


def test_airtel_callback_hash_rejects_tampered_message():
    signature = create_callback_hash("test-private-key", b"original")
    assert not verify_callback_hash("test-private-key", b"tampered", signature)


def test_airtel_callback_hash_rejects_wrong_key():
    signature = create_callback_hash("test-private-key", b"message")
    assert not verify_callback_hash("wrong-key", b"message", signature)


@pytest.mark.parametrize("signature", ["", "not-base64-signature", None])
def test_airtel_callback_hash_rejects_missing_or_invalid_signature(signature):
    assert not verify_callback_hash("test-private-key", b"message", signature)


def test_airtel_callback_hash_requires_bytes_message():
    with pytest.raises(TypeError):
        create_callback_hash("test-private-key", "not-bytes")


def test_airtel_callback_hash_requires_private_key():
    with pytest.raises(ValueError):
        create_callback_hash("", b"message")
