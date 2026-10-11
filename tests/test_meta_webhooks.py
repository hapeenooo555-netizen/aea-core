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


def test_meta_webhook_verification(monkeypatch) -> None:
    monkeypatch.setenv("META_WEBHOOK_VERIFY_TOKEN", "verify-me")
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).get("/webhooks/meta", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "verify-me",
        "hub.challenge": "12345",
    })
    assert response.status_code == 200
    assert response.text == "12345"


def test_meta_webhook_rejects_bad_signature(monkeypatch) -> None:
    monkeypatch.setenv("META_APP_SECRET", "secret")
    app = FastAPI()
    app.include_router(router)
    response = TestClient(app).post("/webhooks/meta", content=b"{}")
    assert response.status_code == 401


def test_meta_webhook_creates_whatsapp_lead(monkeypatch) -> None:
    secret = "secret"
    monkeypatch.setenv("META_APP_SECRET", secret)
    monkeypatch.setenv("HAPE_BROTHERS_OWNER_ID", "hape-owner")
    app = FastAPI()
    app.include_router(router)
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
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    response = TestClient(app).post(
        "/webhooks/meta",
        content=raw,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["processed"] == 1
    assert data["leads"][0]["phone"] == "255700000000"


def test_meta_webhook_fails_closed_when_owner_not_configured(monkeypatch) -> None:
    secret = "secret"
    monkeypatch.setenv("META_APP_SECRET", secret)
    monkeypatch.delenv("HAPE_BROTHERS_OWNER_ID", raising=False)
    app = FastAPI()
    app.include_router(router)
    raw = json.dumps({"entry": []}).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    response = TestClient(app).post(
        "/webhooks/meta",
        content=raw,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "WhatsApp webhook owner is not configured"


def test_meta_webhook_ignores_unconfigured_phone_number_id(monkeypatch) -> None:
    secret = "secret"
    monkeypatch.setenv("META_APP_SECRET", secret)
    monkeypatch.setenv("HAPE_BROTHERS_OWNER_ID", "hape-owner")
    monkeypatch.setenv("META_WA_PHONE_NUMBER_ID", "expected-phone-id")
    app = FastAPI()
    app.include_router(router)
    payload = {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "other-phone-id"},
                    "messages": [{
                        "id": "wamid.other-1",
                        "from": "255700000099",
                        "type": "text",
                        "text": {"body": "Need roofing sheets"},
                    }],
                },
            }],
        }],
    }
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    response = TestClient(app).post(
        "/webhooks/meta",
        content=raw,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.json()["processed"] == 0
    assert response.json()["ignored"] == 1
