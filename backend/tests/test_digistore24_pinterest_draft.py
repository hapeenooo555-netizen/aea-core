from app.services import digistore24_pinterest_draft as draft


class FakeMarketplace:
    configured = True

    def list_entries(self, *, limit=250, sort_by=None):
        return [{
            "id": 123,
            "headline": "AI Marketing Toolkit",
            "description": "Tools for marketing teams",
            "product_category": "AI tools",
            "affiliate_url": "https://affiliate.example/tracking?id=123",
            "image_url": "https://cdn.example/product.jpg",
        }]


def test_build_draft_uses_verified_affiliate_link_and_product_image(monkeypatch):
    monkeypatch.setattr(draft, "_generate_copy", lambda product, *, niche, language: {
        "title": "AI Marketing Toolkit",
        "description": "Explore the toolkit. I may earn a commission if you buy through my affiliate link.",
    })
    result = draft.build_digistore24_pinterest_draft(
        niche="AI tools",
        language="English",
        marketplace=FakeMarketplace(),
    )
    assert result["success"] is True
    assert result["product"]["id"] == "123"
    assert result["draft"]["destination_url"] == "https://affiliate.example/tracking?id=123"
    assert result["draft"]["image_url"] == "https://cdn.example/product.jpg"
    assert result["draft"]["board_id"] is None
    assert result["requires_explicit_approval"] is True
    assert result["published"] is False


def test_build_draft_fails_closed_when_no_verified_affiliate_link():
    class MarketplaceWithoutAffiliateLink:
        configured = True

        def list_entries(self, *, limit=250, sort_by=None):
            return [{"id": 123, "headline": "AI Marketing Toolkit", "sales_page_url": "https://vendor.example"}]

    import pytest
    with pytest.raises(RuntimeError, match="will not fabricate an affiliate link"):
        draft.build_digistore24_pinterest_draft(marketplace=MarketplaceWithoutAffiliateLink())


def test_build_draft_requires_image_or_pexels(monkeypatch):
    monkeypatch.setattr(draft, "_pexels_image", lambda query: None)
    class MarketplaceWithoutImage:
        configured = True

        def list_entries(self, *, limit=250, sort_by=None):
            return [{"id": 123, "headline": "AI Marketing Toolkit", "affiliate_url": "https://affiliate.example/track?id=1"}]

    import pytest
    monkeypatch.setattr(draft, "_generate_copy", lambda product, *, niche, language: {
        "title": "AI Marketing Toolkit",
        "description": "Explore the toolkit. I may earn a commission if you buy through my affiliate link.",
    })
    with pytest.raises(RuntimeError, match="PEXELS_API_KEY"):
        draft.build_digistore24_pinterest_draft(marketplace=MarketplaceWithoutImage())
