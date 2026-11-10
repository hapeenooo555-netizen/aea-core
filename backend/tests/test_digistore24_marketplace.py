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


@pytest.mark.parametrize("operation", ["list_entries", "get_entry"])
def test_client_blocks_vendor_scoped_operations_from_claiming_marketplace_catalog(operation):
    class FakeClient:
        def __init__(self):
            self.called = False

        def get(self, *args, **kwargs):
            self.called = True
            raise AssertionError("Marketplace-wide API request must not be sent")

    fake = FakeClient()
    client = Digistore24MarketplaceClient(api_key="server-secret", client=fake)

    with pytest.raises(Digistore24MarketplaceError, match="third-party apps"):
        if operation == "list_entries":
            client.list_entries(limit=5)
        else:
            client.get_entry(7)

    assert fake.called is False


def test_research_does_not_report_vendor_scoped_api_as_live_affiliate_catalog():
    class FakeClient:
        def get(self, *args, **kwargs):
            raise AssertionError("No unsupported marketplace API call is allowed")

    client = Digistore24MarketplaceClient(api_key="test-key", client=FakeClient())
    with pytest.raises(Digistore24MarketplaceError, match="full affiliate"):
        client.research(target_niches=("Software",), limit=10)


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
