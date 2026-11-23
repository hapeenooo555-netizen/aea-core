"""Deterministic end-to-end creator commerce test with a fake payment provider.

The adapter exists only in tests. It never starts a real payment, exposes no
production route, and can settle only the in-memory fake database below.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import pytest
from copy import deepcopy

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.routers import creator_store


PRODUCT_ID = "22222222-2222-4222-8222-222222222222"
FILE_ID = "33333333-3333-4333-8333-333333333333"
ORDER_ID = "44444444-4444-4444-8444-444444444444"
ITEM_ID = "55555555-5555-4555-8555-555555555555"
TEST_SECRET = b"e2e-only-secret-not-for-deployment"


class Result:
    def __init__(self, data):
        self.data = data


class Query:
    def __init__(self, db, table):
        self.db, self.table_name = db, table
        self.filters, self.payload, self.action = {}, None, "select"
        self.limit_count = None

    def select(self, *_args):
        if self.payload is None:
            self.action = "select"
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def limit(self, count):
        self.limit_count = count
        return self

    def order(self, *_args, **_kwargs):
        return self

    def insert(self, payload):
        self.action, self.payload = "insert", payload
        return self

    def update(self, payload):
        self.action, self.payload = "update", payload
        return self

    def execute(self):
        rows = self.db.tables.setdefault(self.table_name, [])
        matches = [row for row in rows if all(row.get(k) == v for k, v in self.filters.items())]
        if self.action == "insert":
            row = deepcopy(self.payload)
            if self.table_name == "creator_orders":
                row["id"] = ORDER_ID
            elif self.table_name == "creator_order_items":
                row["id"] = ITEM_ID
            else:
                row.setdefault("id", "e2e-row-id")
            rows.append(row)
            return Result([row])
        if self.action == "update":
            for row in matches:
                row.update(self.payload)
            return Result(matches)
        return Result(matches[: self.limit_count] if self.limit_count else matches)


class FakeStorageBucket:
    def create_signed_url(self, path, _expires, _options=None):
        return {"signed_url": "https://private-storage.invalid/" + path}


class FakeStorage:
    def from_(self, _bucket):
        return FakeStorageBucket()


class E2EDatabase:
    def __init__(self):
        self.tables = {
            "creator_products": [{
                "id": PRODUCT_ID,
                "owner_id": "seller-001",
                "title": "AEA Digital Guide",
                "product_type": "guide",
                "price_amount": 15000,
                "currency": "TZS",
                "status": "listed",
                "completion_status": "finished",
            }],
            "creator_product_files": [{
                "id": FILE_ID,
                "product_id": PRODUCT_ID,
                "owner_id": "seller-001",
                "object_path": f"seller-001/{PRODUCT_ID}/guide.pdf",
                "original_filename": "guide.pdf",
                "status": "ready",
            }],
            "creator_orders": [],
            "creator_order_items": [],
            "creator_payment_events": [],
            "creator_product_entitlements": [],
        }
        self.storage = FakeStorage()

    def table(self, name):
        return Query(self, name)

    def rpc(self, name, params):
        assert name == "settle_creator_order"
        order = next(row for row in self.tables["creator_orders"] if row["id"] == params["p_order_id"])
        if params["p_event_type"] != "payment.succeeded" or not params["p_provider_reference"]:
            raise ValueError("invalid successful payment event")
        if order["total_amount"] != params["p_amount"] or order["currency"] != params["p_currency"]:
            raise ValueError("payment amount or currency mismatch")
        event_key = (params["p_provider"], params["p_provider_event_id"])
        prior = next((row for row in self.tables["creator_payment_events"]
                      if (row["provider"], row["provider_event_id"]) == event_key), None)
        if prior:
            if (prior["order_id"] != order["id"] or prior["amount"] != params["p_amount"]
                    or prior["currency"] != params["p_currency"]
                    or prior["provider_reference"] != params["p_provider_reference"]
                    or prior["event_type"] != params["p_event_type"]):
                raise ValueError("provider event replay does not match this order")
            return Result({"success": True, "duplicate": True, "order_id": order["id"]})
        if order["status"] != "pending":
            raise ValueError("order is not payable or was already settled by another event")
        self.tables["creator_payment_events"].append({
            "order_id": order["id"], "provider": params["p_provider"],
            "provider_event_id": params["p_provider_event_id"],
            "provider_reference": params["p_provider_reference"],
            "event_type": params["p_event_type"],
            "amount": params["p_amount"], "currency": params["p_currency"],
        })
        order["status"] = "paid"
        order["paid_at"] = "e2e-test-time"
        item = next(row for row in self.tables["creator_order_items"] if row["order_id"] == order["id"])
        self.tables["creator_product_entitlements"].append({
            "order_item_id": item["id"], "order_id": order["id"],
            "buyer_id": order["buyer_id"], "product_id": item["product_id"],
            "product_file_id": item["product_file_id"], "revoked_at": None,
        })
        return Result({"success": True, "duplicate": False, "order_id": order["id"]})


class FakePaymentProviderE2E:
    """Fake provider that signs callbacks and calls only the fake settlement RPC."""

    def __init__(self, db):
        self.db = db

    @staticmethod
    def sign(event):
        body = json.dumps(event, sort_keys=True, separators=(",", ":")).encode()
        return hmac.new(TEST_SECRET, body, hashlib.sha256).hexdigest()

    def deliver_webhook(self, event, signature):
        expected = self.sign(event)
        if not hmac.compare_digest(expected, signature):
            return {"accepted": False, "reason": "invalid_signature"}
        result = self.db.rpc("settle_creator_order", {
            "p_order_id": event["order_id"],
            "p_provider": "fake_e2e",
            "p_provider_event_id": event["event_id"],
            "p_provider_reference": event["reference"],
            "p_event_type": "payment.succeeded",
            "p_amount": event["amount"],
            "p_currency": event["currency"],
        }).data
        return {"accepted": True, **result}


def make_client(db, user_id="buyer-001"):
    app = FastAPI()
    app.include_router(creator_store.router)
    app.include_router(creator_store.orders_router)
    app.dependency_overrides[get_current_user_id] = lambda: user_id
    app.dependency_overrides[get_user_scoped_client] = lambda: db
    return TestClient(app)


def test_e2e_purchase_fake_payment_and_paid_download(monkeypatch):
    db = E2EDatabase()
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    api = make_client(db)

    # 1. Buyer creates a pending order intent for a real listed/finished product.
    intent = api.post(
        f"/store/products/{PRODUCT_ID}/purchase-intent",
        json={"idempotency_key": "e2e-buyer-guide-0001"},
    )
    assert intent.status_code == 201, intent.text
    order = intent.json()["order"]
    assert order == {"id": ORDER_ID, "status": "pending", "total_amount": 15000, "currency": "TZS"}
    assert intent.json()["payment_initiation_enabled"] is False
    assert intent.json()["delivery_enabled"] is False

    # 2. Pending order cannot download the file.
    denied = api.get(f"/store/orders/{ORDER_ID}/items/{ITEM_ID}/download")
    assert denied.status_code == 403

    # 3. Fake provider sends an invalid signature; order must remain pending.
    event = {
        "event_id": "fake-event-001",
        "order_id": ORDER_ID,
        "reference": "fake-ref-001",
        "amount": 15000,
        "currency": "TZS",
    }
    provider = FakePaymentProviderE2E(db)
    invalid = provider.deliver_webhook(event, "bad-signature")
    assert invalid == {"accepted": False, "reason": "invalid_signature"}
    assert db.tables["creator_orders"][0]["status"] == "pending"
    assert db.tables["creator_product_entitlements"] == []

    # 4. Valid signed fake event settles the order and grants one entitlement.
    settled = provider.deliver_webhook(event, provider.sign(event))
    assert settled["accepted"] is True
    assert settled["success"] is True
    assert settled["duplicate"] is False
    assert db.tables["creator_orders"][0]["status"] == "paid"
    assert len(db.tables["creator_payment_events"]) == 1
    assert len(db.tables["creator_product_entitlements"]) == 1

    # 5. Provider retry is idempotent; it must not duplicate entitlement/event.
    replay = provider.deliver_webhook(event, provider.sign(event))
    assert replay["accepted"] is True
    assert replay["duplicate"] is True
    assert len(db.tables["creator_payment_events"]) == 1
    assert len(db.tables["creator_product_entitlements"]) == 1

    # 6. Paid buyer with matching entitlement gets a short-lived private URL.
    delivered = api.get(f"/store/orders/{ORDER_ID}/items/{ITEM_ID}/download")
    assert delivered.status_code == 200, delivered.text
    assert delivered.json()["expires_in_seconds"] == 300
    assert delivered.json()["download_url"].endswith(f"/{PRODUCT_ID}/guide.pdf")


def test_e2e_fake_payment_rejects_a_second_distinct_event_for_paid_order(monkeypatch):
    db = E2EDatabase()
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    api = make_client(db)
    intent = api.post(
        f"/store/products/{PRODUCT_ID}/purchase-intent",
        json={"idempotency_key": "e2e-second-event-0001"},
    )
    assert intent.status_code == 201
    event = {
        "event_id": "fake-event-original",
        "order_id": ORDER_ID,
        "reference": "fake-ref-original",
        "amount": 15000,
        "currency": "TZS",
    }
    provider = FakePaymentProviderE2E(db)
    first = provider.deliver_webhook(event, provider.sign(event))
    assert first["accepted"] is True
    second_event = {**event, "event_id": "fake-event-second", "reference": "fake-ref-second"}
    with pytest.raises(ValueError, match="already settled by another event"):
        provider.deliver_webhook(second_event, provider.sign(second_event))
    assert len(db.tables["creator_payment_events"]) == 1
    assert len(db.tables["creator_product_entitlements"]) == 1


def test_e2e_fake_payment_rejects_event_replay_with_changed_reference(monkeypatch):
    db = E2EDatabase()
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    api = make_client(db)
    intent = api.post(
        f"/store/products/{PRODUCT_ID}/purchase-intent",
        json={"idempotency_key": "e2e-replay-mismatch-001"},
    )
    assert intent.status_code == 201
    event = {
        "event_id": "fake-event-replay",
        "order_id": ORDER_ID,
        "reference": "fake-ref-original",
        "amount": 15000,
        "currency": "TZS",
    }
    provider = FakePaymentProviderE2E(db)
    provider.deliver_webhook(event, provider.sign(event))
    replay = {**event, "reference": "fake-ref-tampered"}
    with pytest.raises(ValueError, match="provider event replay does not match"):
        provider.deliver_webhook(replay, provider.sign(replay))
    assert len(db.tables["creator_payment_events"]) == 1
    assert len(db.tables["creator_product_entitlements"]) == 1


def test_e2e_purchase_intent_is_idempotent_for_same_buyer_key(monkeypatch):
    db = E2EDatabase()
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    api = make_client(db)
    first = api.post(
        f"/store/products/{PRODUCT_ID}/purchase-intent",
        json={"idempotency_key": "e2e-same-request-0001"},
    )
    second = api.post(
        f"/store/products/{PRODUCT_ID}/purchase-intent",
        json={"idempotency_key": "e2e-same-request-0001"},
    )
    assert first.status_code == 201
    assert second.status_code == 201
    assert second.json()["order"]["id"] == first.json()["order"]["id"]
    assert len(db.tables["creator_orders"]) == 1
    assert len(db.tables["creator_order_items"]) == 1


def test_e2e_order_download_is_not_available_to_another_buyer(monkeypatch):
    db = E2EDatabase()
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    owner_api = make_client(db)
    intent = owner_api.post(
        f"/store/products/{PRODUCT_ID}/purchase-intent",
        json={"idempotency_key": "e2e-owner-check-0001"},
    )
    assert intent.status_code == 201
    order = intent.json()["order"]
    event = {
        "event_id": "fake-event-owner-check",
        "order_id": order["id"],
        "reference": "fake-ref-owner-check",
        "amount": 15000,
        "currency": "TZS",
    }
    provider = FakePaymentProviderE2E(db)
    provider.deliver_webhook(event, provider.sign(event))

    other_buyer_api = make_client(db, user_id="buyer-002")
    denied = other_buyer_api.get(f"/store/orders/{order['id']}/items/{ITEM_ID}/download")
    assert denied.status_code == 403


def test_e2e_creator_cannot_purchase_own_product():
    db = E2EDatabase()
    seller_api = make_client(db, user_id="seller-001")
    response = seller_api.post(
        f"/store/products/{PRODUCT_ID}/purchase-intent",
        json={"idempotency_key": "e2e-self-purchase-0001"},
    )
    assert response.status_code == 409
    assert db.tables["creator_orders"] == []
    assert db.tables["creator_order_items"] == []


def test_e2e_fake_payment_rejects_amount_mismatch():
    db = E2EDatabase()
    db.tables["creator_orders"].append({
        "id": ORDER_ID, "buyer_id": "buyer-001", "seller_id": "seller-001",
        "total_amount": 15000, "currency": "TZS", "status": "pending",
    })
    provider = FakePaymentProviderE2E(db)
    event = {
        "event_id": "fake-event-wrong-amount",
        "order_id": ORDER_ID,
        "reference": "fake-ref-wrong-amount",
        "amount": 1000,
        "currency": "TZS",
    }
    with pytest.raises(ValueError, match="amount or currency mismatch"):
        provider.deliver_webhook(event, provider.sign(event))
    assert db.tables["creator_orders"][0]["status"] == "pending"
    assert db.tables["creator_product_entitlements"] == []


def test_e2e_idempotency_key_cannot_be_reused_for_another_product(monkeypatch):
    db = E2EDatabase()
    other_product_id = "77777777-7777-4777-8777-777777777777"
    other_file_id = "88888888-8888-4888-8888-888888888888"
    db.tables["creator_products"].append({
        "id": other_product_id,
        "owner_id": "seller-002",
        "title": "Another Guide",
        "product_type": "guide",
        "price_amount": 25000,
        "currency": "TZS",
        "status": "listed",
        "completion_status": "finished",
    })
    db.tables["creator_product_files"].append({
        "id": other_file_id,
        "product_id": other_product_id,
        "owner_id": "seller-002",
        "object_path": f"seller-002/{other_product_id}/another.pdf",
        "original_filename": "another.pdf",
        "status": "ready",
    })
    monkeypatch.setattr(creator_store, "get_supabase_product_file_client", lambda: db)
    api = make_client(db)
    key = "e2e-cross-product-key-001"

    first = api.post(
        f"/store/products/{PRODUCT_ID}/purchase-intent",
        json={"idempotency_key": key},
    )
    conflict = api.post(
        f"/store/products/{other_product_id}/purchase-intent",
        json={"idempotency_key": key},
    )

    assert first.status_code == 201
    assert conflict.status_code == 409
    assert "different product" in conflict.json()["detail"]
    assert len(db.tables["creator_orders"]) == 1
    assert len(db.tables["creator_order_items"]) == 1
    assert db.tables["creator_order_items"][0]["product_id"] == PRODUCT_ID
