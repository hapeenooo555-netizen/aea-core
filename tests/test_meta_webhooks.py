from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
import sys
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app import database
from app.routers import meta_webhooks


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(meta_webhooks.router)
    return app


def _signed_post(payload: dict, secret: str = "secret"):
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    response = TestClient(_app()).post(
        "/webhooks/meta",
        content=raw,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )
    return response


def test_meta_webhook_verification_uses_meta_dotted_query_names(monkeypatch) -> None:
    monkeypatch.setenv("META_WEBHOOK_VERIFY_TOKEN", "verify-me")
    response = TestClient(_app()).get("/webhooks/meta", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "verify-me",
        "hub.challenge": "12345",
    })
    assert response.status_code == 200
    assert response.text == "12345"
    assert response.headers["content-type"].startswith("text/plain")


def test_meta_webhook_verification_rejects_wrong_token(monkeypatch) -> None:
    monkeypatch.setenv("META_WEBHOOK_VERIFY_TOKEN", "verify-me")
    response = TestClient(_app()).get("/webhooks/meta", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "wrong-token",
        "hub.challenge": "12345",
    })
    assert response.status_code == 403


def test_meta_webhook_rejects_bad_signature(monkeypatch) -> None:
    monkeypatch.setenv("META_APP_SECRET", "secret")
    response = TestClient(_app()).post("/webhooks/meta", content=b"{}")
    assert response.status_code == 401


def test_meta_webhook_fails_closed_without_valid_owner(monkeypatch) -> None:
    monkeypatch.setenv("META_APP_SECRET", "secret")
    monkeypatch.delenv("HAPE_BROTHERS_OWNER_ID", raising=False)
    response = _signed_post({})
    assert response.status_code == 503
    assert response.json()["detail"] == "Webhook owner is not configured"


def test_meta_webhook_fails_closed_without_persistent_client(monkeypatch) -> None:
    monkeypatch.setenv("META_APP_SECRET", "secret")
    monkeypatch.setenv("HAPE_BROTHERS_OWNER_ID", "d2719a1b-4f2c-4d3e-8f25-90b29e60d8aa")
    monkeypatch.setattr(database, "get_meta_webhook_client", lambda: None)
    response = _signed_post({})
    assert response.status_code == 503
    assert response.json()["detail"] == "Webhook persistence is not configured"


def test_meta_webhook_creates_lead_with_explicit_owner_and_persistent_client(monkeypatch) -> None:
    secret = "secret"
    owner_id = "d2719a1b-4f2c-4d3e-8f25-90b29e60d8aa"
    monkeypatch.setenv("META_APP_SECRET", secret)
    monkeypatch.setenv("HAPE_BROTHERS_OWNER_ID", owner_id)
    client = object()
    monkeypatch.setattr(database, "get_meta_webhook_client", lambda: client)
    observed = {}

    class FakeMarketingOS:
        def __init__(self, *, owner_id, client):
            observed["owner_id"] = owner_id
            observed["client"] = client

        def create_lead(self, **kwargs):
            observed["lead"] = kwargs
            return {"success": True, "mission": {"id": "mission-123"}}

        def qualify_lead(self, lead_id, *, message):
            observed["qualification"] = (lead_id, message)
            return {"success": True, "qualification": {"score": 75}}

    monkeypatch.setattr(meta_webhooks, "HapeBrothersMarketingOS", FakeMarketingOS)
    payload = {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "123"},
                    "messages": [{
                        "id": "wamid.test-1",
                        "from": "255700000000",
                        "type": "text",
                        "text": {"body": "Nahitaji bati gauge 32, sheets 500"},
                    }],
                },
            }],
        }],
    }
    response = _signed_post(payload, secret)
    assert response.status_code == 200
    data = response.json()
    assert data["processed"] == 1
    assert data["leads"][0]["lead_id"] == "mission-123"
    assert observed["owner_id"] == owner_id
    assert observed["client"] is client
    assert observed["lead"]["idempotency_key"] == "whatsapp:wamid.test-1"
    assert observed["qualification"][0] == "mission-123"
