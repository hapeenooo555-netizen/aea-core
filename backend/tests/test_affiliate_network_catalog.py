from app.services.affiliate_networks.catalog import (
    AffiliateNetworkRegistry,
    AffiliateOffer,
    NetworkAccessStatus,
    SUPPORTED_NETWORKS,
)


def test_supported_networks_start_unconfigured_without_credentials():
    registry = AffiliateNetworkRegistry()

    statuses = registry.statuses()

    assert [item["network"] for item in statuses] == list(SUPPORTED_NETWORKS)
    assert all(item["status"] == "not_configured" for item in statuses)
    assert all(item["configured"] is False for item in statuses)
    assert all(item["publisher_approved"] is False for item in statuses)


def test_offer_normalizes_to_provider_neutral_shape():
    offer = AffiliateOffer(
        network="cj",
        external_id="offer-123",
        name="Example AI product",
        category="AI software",
        partnership_status="application_required",
    )

    data = offer.to_dict()

    assert data["network"] == "cj"
    assert data["external_id"] == "offer-123"
    assert data["partnership_status"] == "application_required"


def test_offer_rejects_unknown_network_and_missing_identity():
    import pytest

    with pytest.raises(ValueError, match="Unsupported affiliate network"):
        AffiliateOffer(network="unknown", external_id="1", name="Offer")

    with pytest.raises(ValueError, match="external_id and name are required"):
        AffiliateOffer(network="awin", external_id="", name="Offer")


def test_offer_rejects_unknown_partnership_status():
    import pytest

    with pytest.raises(ValueError, match="Invalid partnership_status"):
        AffiliateOffer(
            network="partnerstack",
            external_id="program-1",
            name="Program",
            partnership_status="auto_approved",
        )


def test_registry_registers_only_supported_networks():
    import pytest

    class FakeConnector:
        network = "cj"

        def health(self):
            return {"network": "cj", "status": "configured"}

        def discover(self, *, category=None, cursor=None, limit=100):
            return {"items": [], "next_cursor": None}

    registry = AffiliateNetworkRegistry()
    connector = FakeConnector()
    registry.register(connector)

    assert registry.get("cj") is connector
    assert registry.get("CJ") is None
    assert registry.statuses()[0]["status"] == "configured"

    connector.network = "other"
    with pytest.raises(ValueError, match="Unsupported affiliate network"):
        registry.register(connector)
