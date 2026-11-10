from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id
from app.routers import affiliate_cj_connection as module


def _client():
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-123"
    return TestClient(app)


def test_status_reports_configured_but_unverified(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-placeholder")
    monkeypatch.setenv("CJ_COMPANY_ID", "123456")

    response = _client().get("/affiliate/cj/connection/status")

    assert response.status_code == 200
    payload = response.json()
    assert payload["credential_configured"] is True
    assert payload["company_id_configured"] is True
    assert payload["authentication_verified"] is False
    assert payload["product_feed_api_verified"] is False
    assert payload["status"] == "ready_to_verify"
    assert "secret-placeholder" not in response.text


def test_verify_requires_token(monkeypatch):
    monkeypatch.delenv("CJ_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.setenv("CJ_COMPANY_ID", "123456")

    response = _client().post("/affiliate/cj/connection/verify")

    assert response.status_code == 503


def test_verify_requires_server_company_id(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-placeholder")
    monkeypatch.delenv("CJ_COMPANY_ID", raising=False)

    response = _client().post(
        "/affiliate/cj/connection/verify", json={"company_id": "123456"}
    )

    assert response.status_code == 503
    assert "CJ_COMPANY_ID" in response.json()["detail"]


def test_verify_rejects_invalid_server_company_id(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-placeholder")
    monkeypatch.setenv("CJ_COMPANY_ID", "not-a-cid")

    response = _client().post("/affiliate/cj/connection/verify")

    assert response.status_code == 503


def test_verify_queries_publisher_product_feeds(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-placeholder")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")
    captured = {}

    def fake_request(query):
        captured["query"] = query
        return {
            "productFeeds": {
                "totalCount": 3,
                "count": 3,
                "resultList": [
                    {"adId": "feed-1", "advertiserId": "111"},
                    {"adId": "feed-2", "advertiserId": "222"},
                    {"adId": "feed-3", "advertiserId": "333"},
                ],
            }
        }

    monkeypatch.setattr(module, "_request_cj", fake_request)
    response = _client().post("/affiliate/cj/connection/verify")

    assert response.status_code == 200
    payload = response.json()
    assert payload["authentication_verified"] is True
    assert payload["product_feed_api_verified"] is True
    assert payload["status"] == "connected"
    assert payload["accessible_feed_count"] == 3
    assert payload["feeds_in_sample"] == 3
    assert 'productFeeds(companyId: 987654)' in captured["query"]
    assert "partnerIds" not in captured["query"]
    assert "secret-placeholder" not in response.text
    assert "987654" not in response.text


def test_verify_does_not_claim_success_for_unexpected_response(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-placeholder")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")
    monkeypatch.setattr(module, "_request_cj", lambda query: {"productFeeds": {}})

    response = _client().post("/affiliate/cj/connection/verify")

    assert response.status_code == 502
    assert response.json()["detail"] == (
        "CJ Product Feed API returned an unexpected feed summary response."
    )


def test_verify_does_not_trust_company_id_from_request_body(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-placeholder")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")
    captured = {}

    def fake_request(query):
        captured["query"] = query
        return {"productFeeds": {"totalCount": 0, "count": 0, "resultList": []}}

    monkeypatch.setattr(module, "_request_cj", fake_request)
    response = _client().post(
        "/affiliate/cj/connection/verify", json={"company_id": "111111"}
    )

    assert response.status_code == 200
    assert 'productFeeds(companyId: "987654")' in captured["query"]
    assert 'companyId: "111111"' not in captured["query"]
