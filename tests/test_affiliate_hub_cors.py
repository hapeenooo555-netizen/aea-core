"""Regression tests for the published Affiliate Hub browser CORS policy."""

from fastapi.testclient import TestClient

from app.main import app


def test_published_affiliate_hub_preflight_allows_authenticated_api_calls() -> None:
    client = TestClient(app)
    response = client.options(
        "/workers",
        headers={
            "Origin": "https://aea-affiliate-hub.lovable.app",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization,content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "https://aea-affiliate-hub.lovable.app"
    assert "authorization" in response.headers.get("access-control-allow-headers", "").lower()


def test_cors_does_not_allow_arbitrary_origins() -> None:
    client = TestClient(app)
    response = client.options(
        "/workers",
        headers={
            "Origin": "https://untrusted-example.invalid",
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )

    assert response.status_code == 400
    assert response.headers.get("access-control-allow-origin") != "https://untrusted-example.invalid"
