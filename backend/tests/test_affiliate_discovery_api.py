from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id
from app.routers.affiliate_discovery import (
    get_affiliate_network_registry,
    router,
)
from app.services.affiliate_networks.catalog import AffiliateNetworkRegistry


class FakeConnector:
    def __init__(self, network, marker):
        self.network = network
        self.marker = marker
        self.calls = []

    def health(self):
        return {
            "network": self.network,
            "configured": True,
            "publisher_approved": True,
            "credential_type": "test_token",
            "status": "connected",
            "message": "Test connector",
        }

    def discover(self, *, category=None, cursor=None, limit=100):
        self.calls.append({"category": category, "cursor": cursor, "limit": limit})
        return {
            "items": [{
                "network": self.network,
                "external_id": self.marker,
                "name": self.marker,
            }],
            "next_cursor": f"{self.marker}-next",
        }


def _client(registry=None, authenticated=True):
    app = FastAPI()
    app.include_router(router)
    if authenticated:
        app.dependency_overrides[get_current_user_id] = lambda: "user-123"
    if registry is not None:
        app.dependency_overrides[get_affiliate_network_registry] = lambda: registry
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


def test_catalog_rejects_unknown_network_with_422():
    response = _client().get("/affiliate/catalog?network=unknown")

    assert response.status_code == 422
    assert response.json()["detail"]["error"] == "unsupported_network"


def test_catalog_requires_authentication():
    response = _client(authenticated=False).get("/affiliate/catalog")

    assert response.status_code in (401, 403)


def test_multi_network_cursor_is_encoded_and_passed_independently():
    registry = AffiliateNetworkRegistry()
    cj = FakeConnector("cj", "cj-offer")
    awin = FakeConnector("awin", "awin-offer")
    registry.register(cj)
    registry.register(awin)

    first = _client(registry).get("/affiliate/catalog?network=cj&network=awin")
    assert first.status_code == 200
    cursor = first.json()["next_cursor"]
    assert cursor
    assert {item["network"] for item in first.json()["items"]} == {"cj", "awin"}

    second = _client(registry).get(
        "/affiliate/catalog?network=cj&network=awin&cursor=" + cursor
    )
    assert second.status_code == 200
    assert cj.calls[1]["cursor"] == "cj-offer-next"
    assert awin.calls[1]["cursor"] == "awin-offer-next"


def test_catalog_rejects_cursor_for_different_network_filter():
    registry = AffiliateNetworkRegistry()
    registry.register(FakeConnector("cj", "cj-offer"))
    cursor = _client(registry).get("/affiliate/catalog?network=cj").json()["next_cursor"]

    response = _client(registry).get(
        "/affiliate/catalog?network=awin&cursor=" + cursor
    )
    assert response.status_code == 422


def test_catalog_rejects_malformed_cursor():
    response = _client().get("/affiliate/catalog?cursor=not-a-valid-cursor")
    assert response.status_code == 422
