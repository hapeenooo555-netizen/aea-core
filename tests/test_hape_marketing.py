"""HAPE BROTHERS Marketing OS unit/API contract tests."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.dependencies import get_current_user, get_current_user_id, get_user_scoped_client
from app.routers.hape_marketing import router
from app.services.hape_marketing_os import HapeBrothersMarketingOS


def _auth_user_id(request: Request) -> str:
    return request.headers["Authorization"][7:]


def _auth_user(request: Request) -> dict[str, Any]:
    return {"id": _auth_user_id(request), "email": "test@example.com", "role": "authenticated"}


def _scoped_client(request: Request) -> Any:
    return None


@pytest.fixture
def client() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = _auth_user_id
    app.dependency_overrides[get_current_user] = _auth_user
    app.dependency_overrides[get_user_scoped_client] = _scoped_client
    test_client = TestClient(app)
    test_client.headers["Authorization"] = "Bearer marketing-owner-a"
    return test_client


def test_channels_are_validated() -> None:
    with pytest.raises(ValueError):
        HapeBrothersMarketingOS("owner-a").normalize_channels(["tiktok"])


def test_campaign_uses_existing_mission_architecture() -> None:
    result = HapeBrothersMarketingOS("owner-a").create_campaign(
        objective="Generate hardware leads in Buguruni",
        channels=["facebook", "instagram", "whatsapp"],
        audience="Hardware shops and contractors",
        call_to_action="WhatsApp us for wholesale supply",
        idempotency_key="campaign-1",
    )
    assert result["success"] is True
    metadata = result["mission"]["metadata"]
    assert metadata["vertical"] == "hape_brothers_marketing"
    assert metadata["resource_type"] == "campaign"
    assert metadata["channels"] == ["facebook", "instagram", "whatsapp"]


def test_lead_capture_and_follow_up_are_owner_scoped() -> None:
    service_a = HapeBrothersMarketingOS("owner-a")
    service_b = HapeBrothersMarketingOS("owner-b")
    created = service_a.create_lead(
        name="Mwanza Hardware",
        phone="255700000000",
        location="Mwanza",
        channel="whatsapp",
        need="Gauge 32 roofing sheets",
        quantity=500,
    )
    lead_id = created["mission"]["id"]
    assert len(service_a.list_leads()) == 1
    assert service_b.list_leads() == []
    follow_up = service_a.follow_up_lead(
        lead_id,
        message="Habari, tunaweza kukutumia quotation ya bati 500.",
    )
    assert follow_up["success"] is True
    assert follow_up["result"]["action"] == "follow_up"


def test_content_draft_contains_channel_safe_fields() -> None:
    result = HapeBrothersMarketingOS("owner-a").create_content(
        channel="instagram",
        topic="Gauge 32 mabati",
        offer="Reliable factory supply",
        call_to_action="Message HAPE BROTHERS on WhatsApp",
    )
    draft = result["mission"]["metadata"]["draft"]
    assert draft["channel"] == "instagram"
    assert draft["hook"]
    assert draft["body"]
    assert draft["cta"]


def test_api_requires_authentication() -> None:
    app = FastAPI()
    app.include_router(router)
    unauthenticated = TestClient(app)
    response = unauthenticated.get("/marketing/dashboard")
    assert response.status_code in {401, 403}


def test_api_creates_lead() -> None:
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "api-owner"
    app.dependency_overrides[get_user_scoped_client] = lambda: None
    tc = TestClient(app)
    response = tc.post("/marketing/leads", json={
        "name": "Buguruni Hardware",
        "phone": "255700000001",
        "location": "Buguruni",
        "channel": "whatsapp",
        "quantity": 500,
    })
    assert response.status_code == 201
    assert response.json()["success"] is True
