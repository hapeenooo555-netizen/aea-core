from __future__ import annotations

import copy
import hashlib
import hmac
import json

from app.services.clickpesa_webhooks import (
    create_payload_checksum,
    verify_payload_checksum,
)


def test_checksum_is_independent_of_object_key_order():
    secret = "local-test-checksum-secret"
    first = {"amount": "25000", "customer": {"name": "Buyer", "phone": "255700000000"}, "status": "PAYMENT RECEIVED"}
    second = {"status": "PAYMENT RECEIVED", "customer": {"phone": "255700000000", "name": "Buyer"}, "amount": "25000"}
    assert create_payload_checksum(secret, first) == create_payload_checksum(secret, second)


def test_checksum_matches_hmac_sha256_of_compact_sorted_json():
    secret = "local-test-checksum-secret"
    payload = {"status": "PAYMENT RECEIVED", "amount": "25000"}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    expected = hmac.new(secret.encode(), canonical.encode(), hashlib.sha256).hexdigest()
    assert create_payload_checksum(secret, payload) == expected


def test_verifier_accepts_valid_checksum_and_ignores_checksum_metadata():
    secret = "local-test-checksum-secret"
    payload = {"amount": "25000", "status": "PAYMENT RECEIVED"}
    payload["checksumMethod"] = "HMAC-SHA256"
    payload["checksum"] = create_payload_checksum(secret, payload)
    assert verify_payload_checksum(secret, payload) is True


def test_verifier_rejects_modified_payload():
    secret = "local-test-checksum-secret"
    payload = {"amount": "25000", "status": "PAYMENT RECEIVED"}
    payload["checksum"] = create_payload_checksum(secret, payload)
    changed = copy.deepcopy(payload)
    changed["amount"] = "1"
    assert verify_payload_checksum(secret, changed) is False


def test_verifier_rejects_missing_malformed_or_wrong_checksum():
    secret = "local-test-checksum-secret"
    assert verify_payload_checksum(secret, {"status": "PAYMENT RECEIVED"}) is False
    assert verify_payload_checksum(secret, {"checksum": "not-a-checksum"}) is False
    assert verify_payload_checksum("", {"checksum": "a" * 64}) is False
