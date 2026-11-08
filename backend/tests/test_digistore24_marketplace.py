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
    entries = client.list_entries(limit=5)
    assert entries == [{"id": 1, "headline": "Example"}]
    url, kwargs = fake.call
    assert url.endswith("/listMarketplaceEntries")
    assert kwargs["headers"]["X-DS-API-KEY"] == "server-secret"
    assert kwargs["params"] == {}


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


def test_client_fetches_marketplace_entry_details():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {"result": "success", "data": {"entry": {"id": 7, "mainProductId": 456, "headline": "Product"}}}

    class FakeClient:
        def __init__(self):
            self.call = None

        def get(self, url, **kwargs):
            self.call = (url, kwargs)
            return FakeResponse()

    fake = FakeClient()
    client = Digistore24MarketplaceClient(api_key="server-secret", client=fake)
    entry = client.get_entry(7)
    assert entry["mainProductId"] == 456
    assert fake.call[0].endswith("/getMarketplaceEntry")
    assert fake.call[1]["params"] == {"entryId": "7"}


def test_research_returns_catalog_entries_even_when_metrics_are_missing():
    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "result": "success",
                "entries": [
                    {
                        "id": 321,
                        "main_product_id": 654,
                        "headline": "Catalog product without performance data",
                        "description": "A listed product with limited KPIs.",
                        "product_category": "Software",
                        "price": 29.0,
                        "currency": "EUR",
                        "affiliate_share": 40,
                    }
                ],
            }

    class FakeClient:
        def get(self, url, **kwargs):
            return FakeResponse()

    result = Digistore24MarketplaceClient(api_key="test-key", client=FakeClient()).research(
        target_niches=("Software",),
        limit=10,
    )

    assert result["marketplace_records_fetched"] == 1
    assert len(result["marketplace_entries"]) == 1
    assert result["marketplace_entries"][0]["marketplace_entry_id"] == "321"
    assert result["marketplace_entries"][0]["product_id"] == "654"
    assert result["marketplace_entries"][0]["name"] == "Catalog product without performance data"
    assert result["marketplace_entries"][0]["price"] == 29.0
    assert result["eligible_count"] == 0
    assert result["candidates"] == []
    assert result["published"] is False


def test_normalize_marketplace_entry_preserves_display_fields():
    record = normalize_marketplace_entry({
        "id": 88,
        "main_product_id": 99,
        "headline": "Product title",
        "description": "Product description",
        "product_category": "Education",
        "price": 17.5,
        "currency": "USD",
        "affiliate_share": 35,
        "image_url": "https://vendor.example/product.png",
        "vendor_name": "Vendor Inc",
    })

    assert record["marketplace_entry_id"] == "88"
    assert record["product_id"] == "99"
    assert record["description"] == "Product description"
    assert record["category"] == "Education"
    assert record["price"] == 17.5
    assert record["currency"] == "USD"
    assert record["image_url"] == "https://vendor.example/product.png"
    assert record["vendor_name"] == "Vendor Inc"
