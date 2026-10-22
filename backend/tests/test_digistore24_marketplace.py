import httpx
import pytest

from app.services.digistore24_marketplace import (
    Digistore24MarketplaceClient,
    Digistore24MarketplaceError,
    normalize_marketplace_entry,
)


def test_normalize_marketplace_entry_maps_real_api_fields():
    record = normalize_marketplace_entry({
        "id": 101,
        "headline": "AI tools for marketers",
        "sales_page_url": "https://vendor.example/sales",
        "product_category": "AI tools",
        "affiliate_share": 60,
        "stats_affiliate_profit_visitor": 2.1,
        "stats_conversion_rate": 4.0,
        "stats_cancel_rate": 2.0,
    })
    assert record["product_id"] == "101"
    assert record["name"] == "AI tools for marketers"
    assert record["earnings_per_click"] == 2.1
    assert record["source_url"] == "https://vendor.example/sales"
    assert record["verified"] is True


def test_client_requires_server_side_api_key():
    client = Digistore24MarketplaceClient(api_key="")
    assert client.configured is False
    with pytest.raises(Digistore24MarketplaceError, match="not configured"):
        client.list_entries()


def test_client_calls_documented_marketplace_operation():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": "success", "data": {"entries": [{"id": 1, "headline": "Example"}]}}

    class FakeClient:
        def __init__(self):
            self.call = None

        def get(self, url, **kwargs):
            self.call = (url, kwargs)
            return FakeResponse()

    fake = FakeClient()
    client = Digistore24MarketplaceClient(api_key="server-secret", client=fake)
    entries = client.list_entries(limit=5, sort_by="stats_stars")
    assert entries == [{"id": 1, "headline": "Example"}]
    url, kwargs = fake.call
    assert url.endswith("/listMarketplaceEntries")
    assert kwargs["headers"]["X-DS-API-KEY"] == "server-secret"
    assert kwargs["params"] == {"sortBy": "stats_stars"}


def test_client_rejects_api_error_without_fabricating_products():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": "error", "message": "invalid key"}

    class FakeClient:
        def get(self, *args, **kwargs):
            return FakeResponse()

    client = Digistore24MarketplaceClient(api_key="secret", client=FakeClient())
    with pytest.raises(Digistore24MarketplaceError, match="did not return a successful"):
        client.list_entries()
