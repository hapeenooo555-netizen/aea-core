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
    monkeypatch.setenv("HAPE_BROTHERS_OWNER_ID", "webhook-test-owner")
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



def test_meta_webhook_requires_explicit_owner(monkeypatch) -> None:
    monkeypatch.setenv("META_APP_SECRET", "secret")
    monkeypatch.delenv("HAPE_BROTHERS_OWNER_ID", raising=False)
    app = FastAPI()
    app.include_router(router)
    payload = {"entry": []}
    raw = json.dumps(payload).encode()
    signature = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
    response = TestClient(app).post(
        "/webhooks/meta",
        content=raw,
        headers={"X-Hub-Signature-256": signature, "Content-Type": "application/json"},
    )
    assert response.status_code == 503


def test_meta_webhook_does_not_fallback_for_unknown_phone_number_owner(monkeypatch) -> None:
    secret = "secret"
    monkeypatch.setenv("META_APP_SECRET", secret)
    monkeypatch.setenv("HAPE_BROTHERS_OWNER_ID", "fallback-owner")
    monkeypatch.setenv("META_PHONE_NUMBER_OWNER_MAP", '{"known-phone-id":"mapped-owner"}')
    app = FastAPI()
    app.include_router(router)
    payload = {
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "unknown-phone-id"},
                    "messages": [{
                        "id": "wamid.unknown-tenant",
                        "from": "255700000002",
                        "type": "text",
                        "text": {"body": "Nahitaji bati 500 gauge 32"},
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

def test_meta_webhook_verification_echoes_non_numeric_challenge_as_plain_text(monkeypatch) -> None:
    monkeypatch.setenv("META_WEBHOOK_VERIFY_TOKEN", "verify-me")
    app = FastAPI()
    app.include_router(router)
    challenge = "verify-challenge-abc"
    response = TestClient(app).get("/webhooks/meta", params={
        "hub.mode": "subscribe",
        "hub.verify_token": "verify-me",
        "hub.challenge": challenge,
    })
    assert response.status_code == 200
    assert response.text == challenge
    assert response.headers["content-type"].startswith("text/plain")
