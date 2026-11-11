from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id
from app.routers import affiliate_cj_promotional_properties as module


def _client():
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-123"
    return TestClient(app)


def test_lists_publisher_promotional_properties_using_server_credentials(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")
    captured = {}

    class Response:
        status_code = 200
        def raise_for_status(self): pass
        def json(self):
            return {"data": {"promotionalProperties": {"totalCount": 1, "resultList": [
                {"id": "101", "publisherId": "987654", "name": "Pinterest profile",
                 "status": "ACTIVE", "propertyTypeDetails": {
                     "type": "SOCIAL_MEDIA", "socialMediaHandle": "@example",
                     "socialMediaPlatform": "PINTEREST"}}
            ]}}}

    def fake_post(url, **kwargs):
        captured["url"] = url
        captured.update(kwargs)
        return Response()

    monkeypatch.setattr(module.httpx, "post", fake_post)
    response = _client().get("/affiliate/cj/promotional-properties")

    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["name"] == "Pinterest profile"
    assert body["items"][0]["id"] == "101"  # internal PID for backend workflows
    assert captured["url"] == module.CJ_PROMOTIONAL_PROPERTIES_URL
    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert "promotionalProperties(publisherId: 987654" in captured["json"]["query"]
    assert "secret-token" not in response.text


def test_promotional_properties_support_search_and_pagination(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")
    captured = {}

    def fake_query(query):
        captured["query"] = query
        return {"promotionalProperties": {"totalCount": 205, "resultList": [{"id": "1", "name": "Website"}]}}

    monkeypatch.setattr(module, "_query_cj", fake_query)
    response = _client().get("/affiliate/cj/promotional-properties?search=pin&status=ACTIVE&limit=50&offset=100")

    assert response.status_code == 200
    assert 'search: "pin"' in captured["query"]
    assert "limit: 50, offset: 100" in captured["query"]
    assert response.json()["next_offset"] == 150


def test_rejects_unsupported_status(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")
    response = _client().get("/affiliate/cj/promotional-properties?status=INVALID")
    assert response.status_code == 422


def test_promotional_properties_requires_authentication(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")
    app = FastAPI()
    app.include_router(module.router)
    response = TestClient(app).get("/affiliate/cj/promotional-properties")
    assert response.status_code in (401, 403)


def test_promotional_properties_handles_cj_forbidden(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret-token")
    monkeypatch.setenv("CJ_COMPANY_ID", "987654")

    class Response:
        status_code = 403
        def raise_for_status(self):
            import httpx
            raise httpx.HTTPStatusError("forbidden", request=None, response=self)

    monkeypatch.setattr(module.httpx, "post", lambda *args, **kwargs: Response())
    response = _client().get("/affiliate/cj/promotional-properties")
    assert response.status_code == 403
    assert "secret-token" not in response.text
