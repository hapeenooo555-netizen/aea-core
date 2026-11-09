from app.services.affiliate_networks.normalizers import (
    normalize_awin_feed_row,
    normalize_cj_product,
    normalize_partnerstack_program,
    normalize_records,
)


def test_normalize_cj_product_keeps_approval_unknown():
    offer = normalize_cj_product({
        "id": "cj-1",
        "title": "AI writing tool",
        "description": "Writing assistant",
        "imageUrl": "https://example.invalid/image.png",
        "link": "https://merchant.invalid/product",
        "currency": "USD",
    })
    assert offer.network == "cj"
    assert offer.external_id == "cj-1"
    assert offer.name == "AI writing tool"
    assert offer.partnership_status == "unknown"


def test_normalize_awin_feed_row_maps_product_fields():
    offer = normalize_awin_feed_row({
        "aw_product_id": "awin-1",
        "product_name": "Automation software",
        "merchant_category": "Software",
        "aw_deep_link": "https://tracking.invalid/deep",
        "currency": "EUR",
    })
    assert offer.network == "awin"
    assert offer.external_id == "awin-1"
    assert offer.affiliate_url == "https://tracking.invalid/deep"
    assert offer.currency == "EUR"
    assert offer.partnership_status == "unknown"


def test_normalize_partnerstack_program_is_not_mislabeled_as_product():
    offer = normalize_partnerstack_program({
        "id": "program-1",
        "name": "SaaS Partner Program",
        "category": "AI",
        "website": "https://vendor.invalid",
    })
    assert offer.network == "partnerstack"
    assert offer.partnership_status == "application_required"
    assert offer.metadata["record_type"] == "program"


def test_normalize_records_rejects_unknown_network_and_bad_rows():
    import pytest

    with pytest.raises(ValueError, match="Unsupported affiliate network"):
        normalize_records("other", [{"id": "1", "name": "X"}])

    with pytest.raises(ValueError, match="external_id and name are required"):
        normalize_records("cj", [{"id": "", "title": "Missing id"}])
