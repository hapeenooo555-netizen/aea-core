import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.dependencies import get_current_user_id
from app.routers import affiliate_discovery as module


def client():
    app = FastAPI()
    app.include_router(module.router)
    app.dependency_overrides[get_current_user_id] = lambda: "user-1"
    return TestClient(app)


def test_discovery_reports_missing_credentials_without_exposing_secrets(monkeypatch):
    for key in ("CJ_PERSONAL_ACCESS_TOKEN", "CJ_COMPANY_ID", "AWIN_PUBLISHER_ID", "AWIN_DATA_FEED_KEY", "PARTNERSTACK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    response = client().get("/affiliate/discovery")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 0
    assert payload["read_only"] is True
    assert all(item["configured"] is False for item in payload["providers"])


def test_cj_advertiser_search_uses_publisher_credentials_and_normalizes_xml(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret")
    monkeypatch.setenv("CJ_COMPANY_ID", "12345")
    captured = {}

    class Response:
        status_code = 200
        text = """<cj-api><advertisers total-matched="1"><advertiser><advertiser-id>88</advertiser-id><advertiser-name>Example</advertiser-name><program-url>https://example.com</program-url><relationship-status>notjoined</relationship-status><primary-category><parent>Software</parent><child>AI</child></primary-category></advertiser></advertisers></cj-api>"""
        def raise_for_status(self): pass

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(module.httpx, "get", fake_get)
    response = client().get("/affiliate/discovery?provider=cj&q=AI")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["name"] == "Example"
    assert item["relationship_status"] == "notjoined"
    assert item["category"] == ["Software", "AI"]
    assert captured["headers"]["Authorization"] == "Bearer secret"
    assert captured["params"]["requestor-cid"] == "12345"
    assert "secret" not in response.text


def test_awin_feed_list_never_returns_feed_download_url(monkeypatch):
    monkeypatch.setenv("AWIN_PUBLISHER_ID", "123")
    monkeypatch.setenv("AWIN_DATA_FEED_KEY", "feed-secret")
    class Response:
        status_code = 200
        text = "Advertiser ID,Advertiser Name,Primary Region,Membership Status,Datafeed Format,Feed ID,Feed Name,Language,Vertical,Last Imported,Last Checked,No of products,URL\n7,Example Shop,GB,active,Awin,55,Main Feed,en,Software,2026-01-01,2026-01-02,4,https://example.com/apikey/feed-secret"
        def raise_for_status(self): pass
    monkeypatch.setattr(module.httpx, "get", lambda *args, **kwargs: Response())
    response = client().get("/affiliate/discovery?provider=awin")
    assert response.status_code == 200
    assert response.json()["items"][0]["name"] == "Example Shop"
    assert "feed-secret" not in response.text
    assert "https://example.com/apikey/feed-secret" not in response.text


def test_partnerstack_program_search_uses_bearer_and_normalizes(monkeypatch):
    monkeypatch.setenv("PARTNERSTACK_API_KEY", "ps-secret")
    captured = {}
    class Response:
        status_code = 200
        def raise_for_status(self): pass
        @staticmethod
        def json():
            return {"data": [{"key": "co_1", "name": "AI Toolkit", "description": "Automation"}]}
    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()
    monkeypatch.setattr(module.httpx, "get", fake_get)
    response = client().get("/affiliate/discovery?provider=partnerstack&q=automation")
    assert response.status_code == 200
    assert response.json()["items"][0]["id"] == "co_1"
    assert captured["headers"]["Authorization"] == "Bearer ps-secret"
    assert "ps-secret" not in response.text



def test_cj_upstream_http_error_exposes_status_but_not_provider_body(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret")
    monkeypatch.setenv("CJ_COMPANY_ID", "12345")

    class Response:
        status_code = 400
        headers = {"content-type": "application/xml"}
        text = "<cj-api><error-message>private provider detail</error-message></cj-api>"
        def raise_for_status(self):
            import httpx
            request = httpx.Request("GET", "https://advertiser-lookup.api.cj.com/v2/advertiser-lookup")
            raise httpx.HTTPStatusError("bad request", request=request, response=httpx.Response(400, request=request))

    monkeypatch.setattr(module.httpx, "get", lambda *args, **kwargs: Response())
    response = client().get("/affiliate/discovery?provider=cj&q=AI")
    assert response.status_code == 502
    assert "upstream HTTP 400" in response.json()["detail"]
    assert "private provider detail" not in response.text
    assert "secret" not in response.text


def test_cj_advertiser_lookup_prefers_explicit_requestor_cid(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret")
    monkeypatch.setenv("CJ_COMPANY_ID", "company-123")
    monkeypatch.setenv("CJ_REQUESTOR_CID", "publisher-456")
    captured = {}

    class Response:
        status_code = 200
        text = "<cj-api><advertisers total-matched=\"0\"></advertisers></cj-api>"
        def raise_for_status(self): pass

    def fake_get(url, **kwargs):
        captured.update(url=url, **kwargs)
        return Response()

    monkeypatch.setattr(module.httpx, "get", fake_get)
    response = client().get("/affiliate/discovery?provider=cj")
    assert response.status_code == 200
    assert captured["params"]["requestor-cid"] == "publisher-456"


def test_cj_error_summary_extracts_xml_message_and_redacts_credentials():
    response = httpx.Response(400, content=b"<cj-api><error-message>Invalid requestor CID 987654 for token abc-secret</error-message></cj-api>", headers={"content-type": "application/xml"})
    summary = module._cj_error_summary(response, token="abc-secret", cid="987654")
    assert summary == "Invalid requestor CID [redacted] for token [redacted]"


def test_cj_error_summary_ignores_unstructured_provider_body():
    response = httpx.Response(400, content=b"<html>bad request</html>")
    assert module._cj_error_summary(response) is None
