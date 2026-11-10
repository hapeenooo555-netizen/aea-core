from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id
from app.routers import affiliate_cj_products as module


def _client():
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-123"
    return TestClient(app)


def test_product_feed_summary_uses_server_credentials_and_documented_query(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")
    captured = {}

    def fake_request(query):
        captured["query"] = query
        return {"productFeeds": {"totalCount": 1, "count": 1, "resultList": [
            {"adId": "42", "advertiserId": "12", "feedName": "Catalog"}
        ]}}

    monkeypatch.setattr(module, "_request_cj", fake_request)
    response = _client().get("/affiliate/cj/product-feeds")

    assert response.status_code == 200
    assert response.json()["items"][0]["feedName"] == "Catalog"
    assert 'productFeeds(companyId: 987654)' in captured["query"]
    assert "secret-token" not in response.text
    assert "987654" not in response.text


def test_products_returns_normalized_provider_result_and_optional_tracking_link(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")
    captured = {}

    def fake_request(query):
        captured["query"] = query
        return {"products": {"totalCount": 2, "resultList": [
            {"id": "p1", "advertiserId": "12", "title": "AI Toolkit",
             "description": "Automation", "imageLink": "https://images.example/p1.jpg", "price": {"amount": "10", "currency": "USD"},
             "linkCode": {"clickUrl": "https://tracking.example/p1"}},
            {"id": "p2", "advertiserId": "13", "title": "Other Product",
             "description": "Other", "price": {"amount": "5", "currency": "USD"}}
        ]}}

    monkeypatch.setattr(module, "_request_cj", fake_request)
    response = _client().get("/affiliate/cj/products?q=AI&advertiser_id=12&pid=555&limit=20&offset=51")

    assert response.status_code == 200
    payload = response.json()
    assert payload["count"] == 1
    assert payload["items"][0]["id"] == "p1"
    assert payload["items"][0]["imageLink"] == "https://images.example/p1.jpg"
    assert "imageLink" in captured["query"]
    assert payload["tracking_links_included"] is True
    assert 'linkCode(pid: "555")' in captured["query"]
    assert "limit: 20, offset: 51" in captured["query"]
    assert payload["limit"] == 20
    assert payload["offset"] == 51
    assert "secret-token" not in response.text


def test_product_endpoints_require_credentials(monkeypatch):
    monkeypatch.delenv("CJ_PERSONAL_ACCESS_TOKEN", raising=False)
    monkeypatch.delenv("CJ_COMPANY_ID", raising=False)

    response = _client().get("/affiliate/cj/product-feeds")

    assert response.status_code == 503
    assert "token" not in response.text.lower() or "credentials" in response.text.lower()


def test_product_feed_endpoint_requires_authentication(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")
    app = FastAPI()
    app.include_router(module.router)

    response = TestClient(app).get("/affiliate/cj/product-feeds")

    assert response.status_code in (401, 403)


def test_cj_product_api_permission_error_is_safe(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")

    class FakeResponse:
        status_code = 403

        def raise_for_status(self):
            import httpx
            raise httpx.HTTPStatusError("forbidden", request=None, response=self)

    monkeypatch.setattr(module.httpx, "post", lambda *args, **kwargs: FakeResponse())
    response = _client().get("/affiliate/cj/product-feeds")

    assert response.status_code == 403
    assert "secret-token" not in response.text
