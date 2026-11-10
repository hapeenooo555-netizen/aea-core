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


def test_cj_discovery_uses_publisher_product_feed_api(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret")
    monkeypatch.setenv("CJ_COMPANY_ID", "12345")
    captured = {}
    def fake_request(query):
        captured["query"] = query
        return {"productFeeds": {"totalCount": 1, "count": 1, "resultList": [{
            "adId": "feed-1", "feedName": "AI Tools Feed", "advertiserId": "88",
            "productCount": 42, "advertiserCountry": "US", "advertiserName": "Example AI"
        }]}}
    monkeypatch.setattr("app.routers.affiliate_cj_products._request_cj", fake_request)
    response = client().get("/affiliate/discovery?provider=cj&q=AI")
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["name"] == "Example AI"
    assert item["item_type"] == "product_feed"
    assert item["product_count"] == 42
    assert "productFeeds(companyId:" in captured["query"]
    assert "advertiser-lookup.api.cj.com" not in captured["query"]


def test_cj_discovery_filters_accessible_feeds_by_query(monkeypatch):
    monkeypatch.setenv("CJ_PERSONAL_ACCESS_TOKEN", "secret")
    monkeypatch.setenv("CJ_COMPANY_ID", "12345")
    monkeypatch.setattr("app.routers.affiliate_cj_products._request_cj", lambda query: {
        "productFeeds": {"totalCount": 2, "resultList": [
            {"adId": "1", "advertiserId": "11", "advertiserName": "AI Tools Co", "feedName": "Main"},
            {"adId": "2", "advertiserId": "22", "advertiserName": "Home Goods", "feedName": "Kitchen"}
        ]}
    })
    response = client().get("/affiliate/discovery?provider=cj&q=AI")
    assert response.status_code == 200
    assert [x["id"] for x in response.json()["items"]] == ["11"]




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

