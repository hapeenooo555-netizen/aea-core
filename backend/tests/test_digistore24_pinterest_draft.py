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



def test_external_pinterest_draft_accepts_user_supplied_affiliate_url(monkeypatch):
    monkeypatch.setattr(draft, "_pexels_image", lambda query: "https://images.example/pin.jpg")
    monkeypatch.setattr(draft, "_generate_copy", lambda product, *, niche, language: {
        "title": "AI Marketing Toolkit",
        "description": "Explore this resource. I may earn a commission if you buy through my affiliate link.",
    })
    result = draft.build_external_pinterest_draft(
        affiliate_url="https://affiliate.example/track?ref=123",
        product_name="AI Marketing Toolkit",
        niche="AI tools",
        language="English",
    )
    assert result["success"] is True
    assert result["draft"]["destination_url"] == "https://affiliate.example/track?ref=123"
    assert result["draft"]["image_url"] == "https://images.example/pin.jpg"
    assert result["published"] is False
    assert result["requires_explicit_approval"] is True
    assert "not independently verified" in result["product"]["source"]


def test_external_pinterest_draft_rejects_invalid_affiliate_url():
    import pytest
    with pytest.raises(ValueError, match="valid affiliate destination URL"):
        draft.build_external_pinterest_draft(affiliate_url="javascript:alert(1)")


def test_external_pinterest_draft_requires_a_public_image(monkeypatch):
    import pytest
    monkeypatch.setattr(draft, "_pexels_image", lambda query: None)
    with pytest.raises(RuntimeError, match="PEXELS_API_KEY"):
        draft.build_external_pinterest_draft(affiliate_url="https://affiliate.example/track")


def test_pexels_image_skips_deepseek_and_prefers_business_photo(monkeypatch):
    import os
    import httpx

    monkeypatch.setenv("PEXELS_API_KEY", "test-key")
    calls = []

    class FakeResponse:
        status_code = 200

        def __init__(self, photos):
            self._photos = photos

        def raise_for_status(self):
            return None

        def json(self):
            return {"photos": self._photos}

    def fake_get(url, *, params, headers, timeout):
        calls.append(params["query"])
        if len(calls) == 1:
            return FakeResponse([
                {"alt": "DeepSeek AI chatbot interface on a laptop", "src": {"large": "https://images.example/deepseek.jpg"}},
                {"alt": "Small business owner working at desk with laptop", "src": {"large": "https://images.example/business.jpg"}},
            ])
        return FakeResponse([])

    monkeypatch.setattr(draft.httpx, "get", fake_get)
    assert draft._pexels_image("Small Business AI and Automation Toolkit") == "https://images.example/business.jpg"
    assert len(calls) == 1


def test_pexels_image_returns_none_when_only_unrelated_ai_chat_photos(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"photos": [
                {"alt": "DeepSeek AI chatbot interface", "src": {"large": "https://images.example/deepseek.jpg"}},
                {"alt": "ChatGPT interface on a screen", "src": {"large": "https://images.example/chatgpt.jpg"}},
            ]}

    monkeypatch.setattr(draft.httpx, "get", lambda *args, **kwargs: FakeResponse())
    assert draft._pexels_image("AI automation toolkit") is None



def test_pexels_image_rejects_robot_image_without_business_relevance(monkeypatch):
    monkeypatch.setenv("PEXELS_API_KEY", "test-key")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"photos": [
                {"alt": "White robot hand with blue lights and digital background", "src": {"large": "https://images.example/robot-hand.jpg"}},
                {"alt": "Abstract blue technology lights", "src": {"large": "https://images.example/blue-lights.jpg"}},
            ]}

    monkeypatch.setattr(draft.httpx, "get", lambda *args, **kwargs: FakeResponse())
    assert draft._pexels_image("Small Business AI and Automation Toolkit") is None


def test_vendor_product_image_accepts_https_cdn_declared_by_vendor_og_image(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {}

        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        assert url.startswith("https://marketplace.iotsystemsgrowth.com/")
        return FakeResponse(
            '<html><head><meta property="og:image" '
            'content="https://cdn.vendor-assets.example/products/toolkit?id=42"></head></html>'
        )

    monkeypatch.setattr(draft.httpx, "get", fake_get)
    image = draft._vendor_product_image(
        "https://marketplace.iotsystemsgrowth.com/small-business-ai-automation-toolkit-digistore/",
        "Small Business AI & Automation Toolkit",
    )
    assert image == "https://cdn.vendor-assets.example/products/toolkit?id=42"


def test_vendor_product_image_rejects_non_https_or_credentialed_og_image(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {}
        text = '<meta property="og:image" content="https://user:pass@cdn.example/image.jpg">'

        def raise_for_status(self):
            return None

    monkeypatch.setattr(draft.httpx, "get", lambda *args, **kwargs: FakeResponse())
    assert draft._vendor_product_image(
        "https://marketplace.iotsystemsgrowth.com/product-digistore/",
        "Small Business AI & Automation Toolkit",
    ) is None


def test_vendor_product_image_accepts_twitter_image_metadata(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {}
        text = '<meta name="twitter:image" content="https://cdn.example/toolkit-image">'

        def raise_for_status(self):
            return None

    monkeypatch.setattr(draft.httpx, "get", lambda *args, **kwargs: FakeResponse())
    assert draft._vendor_product_image(
        "https://marketplace.iotsystemsgrowth.com/product-digistore/",
        "Small Business AI & Automation Toolkit",
    ) == "https://cdn.example/toolkit-image"
