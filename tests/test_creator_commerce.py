from __future__ import annotations

import sys
import pytest
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.main import app
import app.routers.creator_commerce as commerce_router


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


def test_marketplace_upload_rejects_mime_spoofed_pdf_before_database_access():
    db = FakeSupabaseClient(data=[])
    client = _client(user_id="creator-owner", db=db)
    try:
        response = client.put(
            "/commerce/products/b8b4e39e-20ac-46c1-a4fd-8b1a6d6bb6b0/file",
            content=b"not a pdf",
            headers={"content-type": "application/pdf", "content-length": "9"},
        )
        assert response.status_code == 415
        assert "valid PDF signature" in response.json()["detail"]
        assert db.calls == []
    finally:
        _cleanup()


def test_marketplace_upload_rejects_unsupported_mime_type():
    db = FakeSupabaseClient(data=[])
    client = _client(user_id="creator-owner", db=db)
    try:
        response = client.put(
            "/commerce/products/b8b4e39e-20ac-46c1-a4fd-8b1a6d6bb6b0/file",
            content=b"binary",
            headers={"content-type": "application/octet-stream", "content-length": "6"},
        )
        assert response.status_code == 415
        assert db.calls == []
    finally:
        _cleanup()


def test_marketplace_upload_requires_user_scoped_storage_client():
    client = _client(user_id="creator-owner", db=None)
    try:
        response = client.put(
            "/commerce/products/b8b4e39e-20ac-46c1-a4fd-8b1a6d6bb6b0/file",
            content=b"%PDF-1.7",
            headers={"content-type": "application/pdf", "content-length": "8"},
        )
        assert response.status_code == 503
    finally:
        _cleanup()


class FakeSelectQuery:
    def __init__(self, data):
        self.data = data
    def select(self, *_args):
        return self
    def eq(self, *_args):
        return self
    def limit(self, *_args):
        return self
    def execute(self):
        return SimpleNamespace(data=self.data)


class FakeStorageBucket:
    def __init__(self, signed_url):
        self.signed_url = signed_url
        self.calls = []
    def create_signed_url(self, path, expires):
        self.calls.append((path, expires))
        return {"signedURL": self.signed_url}


class FakeServiceClient:
    def __init__(self, entitlements, files=None, signed_url="https://storage.example/signed"):
        self.entitlements = entitlements
        self.files = files or []
        self.bucket = FakeStorageBucket(signed_url)
        self.tables = []
    def table(self, name):
        self.tables.append(name)
        data = self.entitlements if name == "product_entitlements" else self.files
        return FakeSelectQuery(data)
    class _Storage:
        def __init__(self, bucket):
            self.bucket = bucket
        def from_(self, _name):
            return self.bucket
    @property
    def storage(self):
        return self._Storage(self.bucket)


def test_marketplace_download_denies_buyer_without_active_entitlement(monkeypatch):
    service = FakeServiceClient(entitlements=[])
    monkeypatch.setattr(commerce_router, "get_supabase_service_client", lambda: service)
    client = _client(user_id="buyer-without-entitlement")
    try:
        response = client.get("/commerce/orders/c5b8f82a-8b37-4c78-98b8-6a41a0d9cc01/download")
        assert response.status_code == 404
        assert service.bucket.calls == []
        assert "marketplace_product_files" not in service.tables
    finally:
        _cleanup()


def test_marketplace_download_issues_short_url_only_for_active_entitlement(monkeypatch):
    service = FakeServiceClient(
        entitlements=[{"id": "ent-1", "product_id": "product-1", "status": "active"}],
        files=[{"storage_path": "seller/product-1/file.pdf", "content_type": "application/pdf", "file_name": "guide.pdf"}],
    )
    monkeypatch.setattr(commerce_router, "get_supabase_service_client", lambda: service)
    client = _client(user_id="paid-buyer")
    try:
        response = client.get("/commerce/orders/c5b8f82a-8b37-4c78-98b8-6a41a0d9cc01/download")
        assert response.status_code == 200
        body = response.json()
        assert body["download_url"] == "https://storage.example/signed"
        assert body["expires_in_seconds"] == 60
        assert body["file_name"] == "guide.pdf"
        assert service.bucket.calls == [("seller/product-1/file.pdf", 60)]
    finally:
        _cleanup()


def test_marketplace_download_fails_closed_when_storage_returns_non_https_url(monkeypatch):
    service = FakeServiceClient(
        entitlements=[{"id": "ent-1", "product_id": "product-1", "status": "active"}],
        files=[{"storage_path": "seller/product-1/file.pdf", "content_type": "application/pdf", "file_name": "guide.pdf"}],
        signed_url="http://insecure.example/file",
    )
    monkeypatch.setattr(commerce_router, "get_supabase_service_client", lambda: service)
    client = _client(user_id="paid-buyer")
    try:
        response = client.get("/commerce/orders/c5b8f82a-8b37-4c78-98b8-6a41a0d9cc01/download")
        assert response.status_code == 502
        assert "download_url" not in response.json()
    finally:
        _cleanup()
