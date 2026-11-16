from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.main import app


class FakeRpcQuery:
    def __init__(self, data=None, error=None):
        self.data = data
        self.error = error
        self.calls = []

    def execute(self):
        if self.error:
            raise self.error
        return SimpleNamespace(data=self.data)


class FakeSupabaseClient:
    def __init__(self, data=None, error=None):
        self.query = FakeRpcQuery(data=data, error=error)
        self.calls = []

    def rpc(self, name, params):
        self.calls.append((name, params))
        return self.query


def _client(user_id="commerce-buyer", db=None):
    async def current_user_id():
        return user_id

    async def user_scoped_client():
        return db

    app.dependency_overrides[get_current_user_id] = current_user_id
    app.dependency_overrides[get_user_scoped_client] = user_scoped_client
    return TestClient(app)


def _cleanup():
    app.dependency_overrides.pop(get_current_user_id, None)
    app.dependency_overrides.pop(get_user_scoped_client, None)


def test_checkout_creates_pending_sandbox_order_and_uses_rpc():
    db = FakeSupabaseClient(data={
        "order_id": "c5b8f82a-8b37-4c78-98b8-6a41a0d9cc01",
        "status": "pending",
        "total_amount": 25000,
        "currency": "TZS",
        "provider": "test",
        "reused": False,
    })
    client = _client(db=db)
    try:
        response = client.post("/commerce/checkout-sessions", json={
            "product_id": "b8b4e39e-20ac-46c1-a4fd-8b1a6d6bb6b0",
            "idempotency_key": "checkout-test-0001",
            "customer_name": "Buyer",
            "customer_email": "buyer@example.com",
            "customer_phone": "+255700000000",
        })
        assert response.status_code == 201
        body = response.json()
        assert body["success"] is True
        assert body["payment_status"] == "pending"
        assert body["live_payment_enabled"] is False
        assert body["checkout_url"] is None
        assert body["checkout"]["provider"] == "test"
        assert "no download is available" in body["message"].lower()
        assert db.calls == [(
            "create_marketplace_checkout",
            {
                "p_product_id": "b8b4e39e-20ac-46c1-a4fd-8b1a6d6bb6b0",
                "p_idempotency_key": "checkout-test-0001",
            },
        )]
    finally:
        _cleanup()


def test_checkout_rejects_extra_client_supplied_buyer_id():
    client = _client(db=FakeSupabaseClient(data={}))
    try:
        response = client.post("/commerce/checkout-sessions", json={
            "product_id": "b8b4e39e-20ac-46c1-a4fd-8b1a6d6bb6b0",
            "idempotency_key": "checkout-test-0002",
            "customer_name": "Buyer",
            "customer_email": "buyer@example.com",
            "customer_phone": "+255700000000",
            "buyer_id": "forged-buyer",
        })
        assert response.status_code == 422
    finally:
        _cleanup()


def test_checkout_rejects_invalid_idempotency_key():
    client = _client(db=FakeSupabaseClient(data={}))
    try:
        response = client.post("/commerce/checkout-sessions", json={
            "product_id": "b8b4e39e-20ac-46c1-a4fd-8b1a6d6bb6b0",
            "idempotency_key": "short",
            "customer_name": "Buyer",
            "customer_email": "buyer@example.com",
            "customer_phone": "+255700000000",
        })
        assert response.status_code == 422
    finally:
        _cleanup()


def test_checkout_requires_user_scoped_database_client():
    client = _client(db=None)
    try:
        response = client.post("/commerce/checkout-sessions", json={
            "product_id": "b8b4e39e-20ac-46c1-a4fd-8b1a6d6bb6b0",
            "idempotency_key": "checkout-test-0003",
            "customer_name": "Buyer",
            "customer_email": "buyer@example.com",
            "customer_phone": "+255700000000",
        })
        assert response.status_code == 503
    finally:
        _cleanup()


def test_checkout_hides_database_exception_details():
    db = FakeSupabaseClient(error=RuntimeError("private database detail"))
    client = _client(db=db)
    try:
        response = client.post("/commerce/checkout-sessions", json={
            "product_id": "b8b4e39e-20ac-46c1-a4fd-8b1a6d6bb6b0",
            "idempotency_key": "checkout-test-0004",
            "customer_name": "Buyer",
            "customer_email": "buyer@example.com",
            "customer_phone": "+255700000000",
        })
        assert response.status_code == 409
        assert response.json()["detail"] == "Checkout session could not be created."
        assert "private database detail" not in response.text
    finally:
        _cleanup()


def test_checkout_requires_database_to_confirm_order_id():
    db = FakeSupabaseClient(data=None)
    client = _client(db=db)
    try:
        response = client.post("/commerce/checkout-sessions", json={
            "product_id": "b8b4e39e-20ac-46c1-a4fd-8b1a6d6bb6b0",
            "idempotency_key": "checkout-test-0005",
            "customer_name": "Buyer",
            "customer_email": "buyer@example.com",
            "customer_phone": "+255700000000",
        })
        assert response.status_code == 502
    finally:
        _cleanup()
