from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id
from app.routers import affiliate_cj_connection as module


def _client():
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-123"
    return TestClient(app)


def test_status_reports_token_present_but_does_not_claim_authentication(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-placeholder")
    monkeypatch.delenv("CJ_COMPANY_ID", raising=False)

    response = _client().get("/affiliate/cj/connection/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["credential_configured"] is True
    assert payload["company_id_configured"] is False
    assert payload["authentication_verified"] is False
    assert payload["status"] == "company_id_required"
    assert "secret-placeholder" not in response.text


def test_verify_requires_configured_token(monkeypatch):
    monkeypatch.delenv("CJ_PERSONAL_ACCESS_TOKEN", raising=False)

    response = _client().post(
        "/affiliate/cj/connection/verify", json={"company_id": "123456"}
    )

    assert response.status_code == 503


def test_verify_requires_numeric_company_id(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-placeholder")

    response = _client().post(
        "/affiliate/cj/connection/verify", json={"company_id": "not-a-cid"}
    )

    assert response.status_code == 422


def test_verify_sends_bearer_token_and_returns_safe_success(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-placeholder")
    captured = {}

    class FakeResponse:
        status_code = 200

        @staticmethod
        def json():
            return {"data": {"promotionalProperties": {"totalCount": 1}}}

    def fake_post(url, *, headers, json, timeout):
        captured.update(url=url, headers=headers, json=json, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(module.httpx, "post", fake_post)

    response = _client().post(
        "/affiliate/cj/connection/verify", json={"company_id": "123456"}
    )

    assert response.status_code == 200
    assert response.json()["authentication_verified"] is True
    assert response.json()["promotional_properties_found"] == 1
    assert captured["headers"]["Authorization"] == "Bearer secret-placeholder"
    assert "secret-placeholder" not in response.text


def test_verify_maps_invalid_token_to_401(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-placeholder")

    class FakeResponse:
        status_code = 401

    monkeypatch.setattr(module.httpx, "post", lambda *args, **kwargs: FakeResponse())

    response = _client().post(
        "/affiliate/cj/connection/verify", json={"company_id": "123456"}
    )

    assert response.status_code == 401
    assert "secret-placeholder" not in response.text


def test_verify_maps_provider_permission_error_to_403(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-placeholder")

    class FakeResponse:
        status_code = 403

    monkeypatch.setattr(module.httpx, "post", lambda *args, **kwargs: FakeResponse())

    response = _client().post(
        "/affiliate/cj/connection/verify", json={"company_id": "123456"}
    )

    assert response.status_code == 403
