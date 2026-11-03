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


import pytest


def _preflight(origin: str):
    return TestClient(app).options(
        "/workers",
        headers={"Origin": origin, "Access-Control-Request-Method": "GET", "Access-Control-Request-Headers": "authorization,content-type"},
    )


@pytest.mark.parametrize("origin", [
    "https://15f25893-acd7-4e76-b02a-cce7d9dbc38d.lovableproject.com",
    "https://id-preview--15f25893-acd7-4e76-b02a-cce7d9dbc38d.lovable.app",
    "https://id-preview-0c4cb0e6--15f25893-acd7-4e76-b02a-cce7d9dbc38d.lovable.app",
])
def test_hub_project_preview_origins_are_allowed(origin: str) -> None:
    response = _preflight(origin)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == origin


@pytest.mark.parametrize("origin", [
    "https://00000000-0000-0000-0000-000000000000.lovableproject.com",
    "https://id-preview--other-project.lovable.app",
    "https://15f25893-acd7-4e76-b02a-cce7d9dbc38d.lovableproject.com.evil.example",
    "http://15f25893-acd7-4e76-b02a-cce7d9dbc38d.lovableproject.com",
])
def test_other_lovable_projects_are_not_allowed(origin: str) -> None:
    assert _preflight(origin).status_code == 400
