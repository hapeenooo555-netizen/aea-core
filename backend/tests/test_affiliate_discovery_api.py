from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id
from app.routers.affiliate_discovery import router


def _client():
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-123"
    return TestClient(app)


def test_network_status_endpoint_reports_unconfigured_without_secrets():
    response = _client().get("/affiliate/networks")

    assert response.status_code == 200
    payload = response.json()
    assert [item["network"] for item in payload["networks"]] == [
        "cj", "awin", "partnerstack"
    ]
    assert all(item["status"] == "not_configured" for item in payload["networks"])
    assert all("api_key" not in item and "token" not in item for item in payload["networks"])


def test_catalog_endpoint_is_empty_until_connectors_are_configured():
    response = _client().get("/affiliate/catalog?category=AI&limit=20")

    assert response.status_code == 200
    payload = response.json()
    assert payload["items"] == []
    assert payload["count"] == 0
    assert payload["next_cursor"] is None
    assert len(payload["networks"]) == 3


def test_catalog_rejects_unknown_network_without_querying_providers():
    response = _client().get("/affiliate/catalog?network=unknown")

    assert response.status_code == 200
    assert response.json()["error"] == "unsupported_network"
    assert response.json()["unsupported_networks"] == ["unknown"]
