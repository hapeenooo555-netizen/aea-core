from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.main import app
from app.database import get_supabase_service_client
from app.services.clickpesa_webhooks import create_payload_checksum


class FakeTableQuery:
    def __init__(self, table, calls):
        self.table = table
        self.calls = calls
        self.row = None
        self.options = {}

    def upsert(self, row, **kwargs):
        self.row = row
        self.options = kwargs
        return self

    def execute(self):
        self.calls.append((self.table, self.row, self.options))
        return SimpleNamespace(data=[])


class FakeServiceClient:
    def __init__(self):
        self.calls = []

    def table(self, name):
        return FakeTableQuery(name, self.calls)


def test_clickpesa_webhook_checks_checksum_and_inboxes_without_marking_paid(monkeypatch):
    monkeypatch.setenv("CLICKPESA_CHECKSUM_KEY", "test-checksum-secret")
    fake = FakeServiceClient()
    monkeypatch.setattr("app.routers.creator_commerce.get_supabase_service_client", lambda: fake)
    order_id = str(uuid4())
    payload = {
        "event": "PAYMENT RECEIVED",
        "data": {
            "paymentReference": "CP-PAY-001",
            "orderReference": order_id,
            "collectedAmount": "25000",
            "collectedCurrency": "TZS",
            "status": "SUCCESS",
        },
    }
    payload["checksum"] = create_payload_checksum("test-checksum-secret", payload)
    response = TestClient(app).post("/commerce/webhooks/clickpesa", json=payload)
    assert response.status_code == 202
    body = response.json()
    assert body["accepted"] is True
    assert body["payment_status"] == "pending_verification"
    assert body["order_status_changed"] is False
    assert body["entitlement_granted"] is False
    assert fake.calls[0][0] == "payment_events"
    assert fake.calls[0][1]["order_id"] == order_id
    assert fake.calls[0][1]["verification_status"] == "received"
    assert fake.calls[0][1]["amount"] == 25000


def test_clickpesa_webhook_rejects_invalid_checksum(monkeypatch):
    monkeypatch.setenv("CLICKPESA_CHECKSUM_KEY", "test-checksum-secret")
    monkeypatch.setattr("app.routers.creator_commerce.get_supabase_service_client", lambda: FakeServiceClient())
    response = TestClient(app).post("/commerce/webhooks/clickpesa", json={
        "event": "PAYMENT RECEIVED",
        "data": {"orderReference": str(uuid4()), "paymentReference": "CP-001"},
        "checksum": "0" * 64,
    })
    assert response.status_code == 401


def test_clickpesa_webhook_rejects_unsupported_event(monkeypatch):
    monkeypatch.setenv("CLICKPESA_CHECKSUM_KEY", "test-checksum-secret")
    payload = {"event": "PAYOUT INITIATED", "data": {}}
    payload["checksum"] = create_payload_checksum("test-checksum-secret", payload)
    response = TestClient(app).post("/commerce/webhooks/clickpesa", json=payload)
    assert response.status_code == 400


def test_clickpesa_webhook_rejects_negative_amount(monkeypatch):
    monkeypatch.setenv("CLICKPESA_CHECKSUM_KEY", "test-checksum-secret")
    payload = {
        "event": "PAYMENT RECEIVED",
        "data": {
            "paymentReference": "CP-PAY-002",
            "orderReference": str(uuid4()),
            "collectedAmount": "-10",
            "collectedCurrency": "TZS",
        },
    }
    payload["checksum"] = create_payload_checksum("test-checksum-secret", payload)
    response = TestClient(app).post("/commerce/webhooks/clickpesa", json=payload)
    assert response.status_code == 400


def test_clickpesa_webhook_fails_closed_without_storage(monkeypatch):
    monkeypatch.setenv("CLICKPESA_CHECKSUM_KEY", "test-checksum-secret")
    monkeypatch.setattr("app.routers.creator_commerce.get_supabase_service_client", lambda: None)
    payload = {
        "event": "PAYMENT FAILED",
        "data": {"id": "CP-PAY-003", "orderReference": str(uuid4()), "status": "FAILED"},
    }
    payload["checksum"] = create_payload_checksum("test-checksum-secret", payload)
    response = TestClient(app).post("/commerce/webhooks/clickpesa", json=payload)
    assert response.status_code == 503
