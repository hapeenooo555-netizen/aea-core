from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.routers.meta_webhooks import router

OWNER_ID = "28c40754-274c-4681-896a-a86ffd240818"


def _signed_post(client, payload, secret="test-secret"):
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post(
        "/webhooks/meta",
        content=raw,
        headers={
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )


def _message_payload(phone_number_id="123", message_id="wamid.test-1"):
    return {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": phone_number_id},
                    "messages": [{
                        "id": message_id,
                        "from": "255700000000",
                        "type": "text",
                        "text": {"body": "Nahitaji mabati gauge 32, sheets 500"},
                    }],
                },
            }],
        }],
    }


def test_meta_webhook_verification_returns_plain_text_challenge(monkeypatch):
    monkeypatch.setenv("META_WEBHOOK_VERIFY_TOKEN", "verify-me")
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get(
        "/webhooks/meta",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "verify-me",
            "hub.challenge": "12345",
        },
    )
    assert response.status_code == 200
    assert response.text == "12345"
    assert response.headers["content-type"].startswith("text/plain")


def test_meta_webhook_verification_rejects_wrong_token(monkeypatch):
    monkeypatch.setenv("META_WEBHOOK_VERIFY_TOKEN", "verify-me")
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get(
        "/webhooks/meta",
        params={
            "hub.mode": "subscribe",
            "hub.verify_token": "wrong",
            "hub.challenge": "12345",
        },
    )
    assert response.status_code == 403


def test_meta_webhook_rejects_bad_signature(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "test-secret")
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post("/webhooks/meta", content=b"{}")
    assert response.status_code == 401


def test_meta_webhook_persists_lead_with_explicit_owner(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "test-secret")
    monkeypatch.setenv("HAPE_BROTHERS_OWNER_ID", OWNER_ID)
    persistent_client = object()
    monkeypatch.setattr(
        "app.routers.meta_webhooks.get_supabase_service_client",
        lambda: persistent_client,
    )

    class FakeMarketingOS:
        def __init__(self, owner_id, client):
            assert owner_id == OWNER_ID
            assert client is persistent_client

        def create_lead(self, **kwargs):
            assert kwargs["idempotency_key"] == "whatsapp:123:wamid.test-1"
            return {"success": True, "mission": {"id": "lead-123"}}

        def qualify_lead(self, lead_id, *, message=None):
            assert lead_id == "lead-123"
            assert message == "Nahitaji mabati gauge 32, sheets 500"
            return {
                "success": True,
                "mission": {
                    "id": "lead-123",
                    "result": {
                        "action": "lead_qualified",
                        "qualification": {"tier": "high"},
                    },
                },
            }

    monkeypatch.setattr("app.routers.meta_webhooks.HapeBrothersMarketingOS", FakeMarketingOS)
    app = FastAPI()
    app.include_router(router)
    response = _signed_post(TestClient(app), _message_payload())

    assert response.status_code == 200
    data = response.json()
    assert data["processed"] == 1
    assert data["leads"][0]["lead_id"] == "lead-123"
    assert data["leads"][0]["qualification"]["tier"] == "high"
    assert data["leads"][0]["follow_up_status"] == "not_started"


def test_meta_webhook_fails_closed_without_service_client(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "test-secret")
    monkeypatch.setenv("HAPE_BROTHERS_OWNER_ID", OWNER_ID)
    monkeypatch.setattr("app.routers.meta_webhooks.get_supabase_service_client", lambda: None)
    app = FastAPI()
    app.include_router(router)
    response = _signed_post(TestClient(app), _message_payload())
    assert response.status_code == 503


def test_meta_webhook_rejects_invalid_owner_uuid(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "test-secret")
    monkeypatch.setenv("HAPE_BROTHERS_OWNER_ID", "not-a-uuid")
    monkeypatch.setattr("app.routers.meta_webhooks.get_supabase_service_client", lambda: object())
    app = FastAPI()
    app.include_router(router)
    response = _signed_post(TestClient(app), _message_payload())
    assert response.status_code == 503


def test_meta_webhook_does_not_cross_assign_unknown_mapped_asset(monkeypatch):
    monkeypatch.setenv("META_APP_SECRET", "test-secret")
    monkeypatch.setenv("HAPE_BROTHERS_OWNER_ID", OWNER_ID)
    monkeypatch.setenv("META_PHONE_NUMBER_OWNER_MAP", json.dumps({"other-phone-id": OWNER_ID}))
    monkeypatch.setattr("app.routers.meta_webhooks.get_supabase_service_client", lambda: object())
    app = FastAPI()
    app.include_router(router)
    response = _signed_post(TestClient(app), _message_payload(phone_number_id="123"))
    assert response.status_code == 200
    assert response.json()["processed"] == 0
    assert response.json()["ignored"] == 1
