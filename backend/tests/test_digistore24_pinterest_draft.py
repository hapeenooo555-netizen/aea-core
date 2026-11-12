from app.services import digistore24_pinterest_draft as draft


class FakeMarketplace:
    configured = True

    def list_entries(self, *, limit=250, sort_by=None):
        return [{
            "id": 123,
            "main_product_id": 456,
            "headline": "AI Marketing Toolkit",
            "description": "Tools for marketing teams",
            "product_category": "AI tools",
            "affiliate_url": "https://affiliate.example/tracking?id=123",
            "image_url": "https://cdn.example/product.jpg",
        }]


def _fake_copy(product, *, niche, language):
    return {
        "title": "AI Marketing Toolkit",
        "description": "Explore the toolkit. I may earn a commission if you buy through my affiliate link.",
    }


def test_build_draft_uses_verified_affiliate_link_and_product_image(monkeypatch):
    monkeypatch.setattr(draft, "_generate_copy", _fake_copy)
    result = draft.build_digistore24_pinterest_draft(
        niche="AI tools",
        language="English",
        marketplace=FakeMarketplace(),
    )
    assert result["success"] is True
    assert result["product"]["id"] == "456"
    assert result["draft"]["destination_url"] == "https://affiliate.example/tracking?id=123"
    assert result["draft"]["image_url"] == "https://cdn.example/product.jpg"
    assert result["draft"]["board_id"] is None
    assert result["requires_explicit_approval"] is True
    assert result["published"] is False


def test_build_draft_fails_closed_when_no_verified_affiliate_link(monkeypatch):
    class MarketplaceWithoutAffiliateLink:
        configured = True

        def list_entries(self, *, limit=250, sort_by=None):
            return [{"id": 123, "main_product_id": 456, "headline": "AI Marketing Toolkit", "sales_page_url": "https://vendor.example"}]

    import pytest
    monkeypatch.delenv("DIGISTORE24_AFFILIATE_ID", raising=False)
    monkeypatch.delenv("DIGISTORE24_APPROVED_PRODUCT_IDS", raising=False)
    with pytest.raises(RuntimeError, match="DIGISTORE24_AFFILIATE_ID"):
        draft.build_digistore24_pinterest_draft(marketplace=MarketplaceWithoutAffiliateLink())


def test_build_draft_uses_fallback_only_for_allowlisted_approved_product(monkeypatch):
    import pytest
    monkeypatch.setenv("DIGISTORE24_AFFILIATE_ID", "affiliate-abc")
    monkeypatch.setenv("DIGISTORE24_APPROVED_PRODUCT_IDS", "456,789")
    monkeypatch.setattr(draft, "_generate_copy", _fake_copy)
    monkeypatch.setattr(draft, "_pexels_image", lambda query: None)

    class MarketplaceWithoutAffiliateLink:
        configured = True

        def list_entries(self, *, limit=250, sort_by=None):
            return [{
                "id": 123,
                "main_product_id": 456,
                "headline": "AI Marketing Toolkit",
                "image_url": "https://cdn.example/product.jpg",
            }]

    result = draft.build_digistore24_pinterest_draft(marketplace=MarketplaceWithoutAffiliateLink())
    assert result["draft"]["destination_url"] == "https://www.checkout-ds24.com/redir/456/affiliate-abc/Pinterest"
    assert result["product"]["id"] == "456"
    assert result["published"] is False


def test_build_draft_does_not_fallback_for_product_not_allowlisted(monkeypatch):
    import pytest
    monkeypatch.setenv("DIGISTORE24_AFFILIATE_ID", "affiliate-abc")
    monkeypatch.setenv("DIGISTORE24_APPROVED_PRODUCT_IDS", "789")

    class MarketplaceWithoutAffiliateLink:
        configured = True

        def list_entries(self, *, limit=250, sort_by=None):
            return [{
                "id": 123,
                "main_product_id": 456,
                "headline": "AI Marketing Toolkit",
                "sales_page_url": "https://vendor.example",
            }]

    with pytest.raises(RuntimeError, match="DIGISTORE24_APPROVED_PRODUCT_IDS"):
        draft.build_digistore24_pinterest_draft(marketplace=MarketplaceWithoutAffiliateLink())


def test_build_draft_requires_image_or_pexels(monkeypatch):
    monkeypatch.setattr(draft, "_pexels_image", lambda query: None)

    class MarketplaceWithoutImage:
        configured = True

        def list_entries(self, *, limit=250, sort_by=None):
            return [{"id": 123, "main_product_id": 456, "headline": "AI Marketing Toolkit", "affiliate_url": "https://affiliate.example/track?id=1"}]

    import pytest
    monkeypatch.setattr(draft, "_generate_copy", _fake_copy)
    with pytest.raises(RuntimeError, match="PEXELS_API_KEY"):
        draft.build_digistore24_pinterest_draft(marketplace=MarketplaceWithoutImage())



def test_external_pinterest_draft_uses_vendor_product_artwork(monkeypatch):
    monkeypatch.setattr(draft, "_vendor_product_details", lambda url: {"name": "AI Marketing Toolkit", "description": "Automates marketing workflows.", "image_url": "https://cdn.vendor.example/toolkit.png"})
    monkeypatch.setattr(draft, "_vendor_product_image", lambda url, name: None)
    monkeypatch.setattr(draft, "_pexels_image", lambda query: (_ for _ in ()).throw(AssertionError("stock fallback must not run")))
    monkeypatch.setattr(draft, "_generate_copy", lambda product, *, niche, language: {
        "title": "AI Marketing Toolkit",
        "description": "Explore this resource. I may earn a commission if you buy through my affiliate link.",
    })
    result = draft.build_external_pinterest_draft(
        affiliate_url="https://marketplace.iotsystemsgrowth.com/toolkit-digistore/",
        niche="AI tools",
        language="English",
    )
    assert result["success"] is True
    assert result["draft"]["destination_url"] == "https://marketplace.iotsystemsgrowth.com/toolkit-digistore/"
    assert result["draft"]["image_url"] == "https://cdn.vendor.example/toolkit.png"
    assert result["draft"]["image_source"] == "vendor_product_page"
    assert result["product"]["name"] == "AI Marketing Toolkit"
    assert result["published"] is False
    assert result["requires_explicit_approval"] is True
    assert "not independently verified" in result["product"]["source"]


def test_external_pinterest_draft_rejects_invalid_affiliate_url():
    import pytest
    with pytest.raises(ValueError, match="valid affiliate destination URL"):
        draft.build_external_pinterest_draft(affiliate_url="javascript:alert(1)")


def test_external_pinterest_draft_fails_closed_without_verified_product_artwork(monkeypatch):
    import pytest
    monkeypatch.setattr(draft, "_vendor_product_details", lambda url: {"name": "Product", "description": "A real product description.", "image_url": ""})
    monkeypatch.setattr(draft, "_vendor_product_image", lambda url, name: None)
    monkeypatch.setattr(draft, "_pexels_image", lambda query: (_ for _ in ()).throw(AssertionError("stock fallback must not run")))
    with pytest.raises(RuntimeError, match="could not verify a product image"):
        draft.build_external_pinterest_draft(affiliate_url="https://marketplace.iotsystemsgrowth.com/track")


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


def test_external_pinterest_drafts_returns_three_core_generated_options(monkeypatch):
    calls = []

    monkeypatch.setattr(draft, "_vendor_product_details", lambda url: {"name": "Small Business AI & Automation Toolkit", "description": "Automate repetitive small-business workflows.", "image_url": "https://cdn.vendor.example/toolkit.png"})
    monkeypatch.setattr(draft, "_vendor_product_image", lambda url, name: None)

    def fake_copy(product, *, niche, language):
        calls.append(niche)
        return {"title": f"Option {len(calls)}", "description": "I may earn a commission through this affiliate link."}

    monkeypatch.setattr(draft, "_generate_copy", fake_copy)
    result = draft.build_external_pinterest_drafts(
        affiliate_url="https://marketplace.iotsystemsgrowth.com/toolkit-digistore/",
        niche="AI tools and business automation",
        language="English",
    )
    assert len(result["pins"]) == 3
    assert [pin["title"] for pin in result["pins"]] == ["Option 1", "Option 2", "Option 3"]
    assert len(calls) == 3
    assert result["image_url"] == "https://cdn.vendor.example/toolkit.png"
    assert result["published"] is False
    assert result["requires_explicit_approval"] is True


def test_external_pinterest_drafts_make_repeated_titles_distinct(monkeypatch):
    monkeypatch.setattr(draft, "_vendor_product_details", lambda url: {"name": "Small Business AI & Automation Toolkit", "description": "Automate repetitive small-business workflows.", "image_url": "https://cdn.vendor.example/toolkit.png"})
    monkeypatch.setattr(draft, "_vendor_product_image", lambda url, name: None)
    monkeypatch.setattr(draft, "_generate_copy", lambda product, *, niche, language: {
        "title": "Boost Your Small Business with the AI & Automation Toolkit",
        "description": "A useful toolkit for productivity. I may earn a commission through this affiliate link.",
    })
    result = draft.build_external_pinterest_drafts(
        affiliate_url="https://marketplace.iotsystemsgrowth.com/toolkit-digistore/",
        product_name="Small Business AI & Automation Toolkit",
        product_description="Automate repetitive small-business workflows.",
        niche="AI tools and business automation",
        language="English",
    )
    titles = [pin["title"] for pin in result["pins"]]
    assert len(set(titles)) == 3
    assert titles[0].startswith("Boost Productivity with ")
    assert titles[1].startswith("Save Time with ")
    assert titles[2].startswith("Grow Your Business with ")


def test_vendor_product_details_extracts_jsonld_product_metadata(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {}
        text = """
        <html><head>
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Product",
         "name":"Small Business AI Toolkit",
         "description":"A toolkit for automating small business workflows.",
         "image":"https://cdn.example/toolkit.png"}
        </script>
        </head><body><h1>Fallback heading</h1></body></html>
        """

        def raise_for_status(self):
            return None

    monkeypatch.setattr(draft.httpx, "get", lambda *args, **kwargs: FakeResponse())
    result = draft._vendor_product_details(
        "https://marketplace.iotsystemsgrowth.com/small-business-ai-automation-toolkit-digistore/"
    )
    assert result == {
        "name": "Small Business AI Toolkit",
        "description": "A toolkit for automating small business workflows.",
        "image_url": "https://cdn.example/toolkit.png",
    }


def test_vendor_product_details_falls_back_to_semantic_html(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {}
        text = """
        <html><head><title>Vendor page</title></head>
        <body>
          <h1>Small Business AI and Automation Toolkit</h1>
          <div class="woocommerce-product-details__short-description">
            <p>Automate repetitive admin and marketing tasks for a small business.</p>
          </div>
          <img src="https://cdn.example/toolkit.png" alt="Product artwork">
        </body></html>
        """

        def raise_for_status(self):
            return None

    monkeypatch.setattr(draft.httpx, "get", lambda *args, **kwargs: FakeResponse())
    result = draft._vendor_product_details(
        "https://marketplace.iotsystemsgrowth.com/small-business-ai-automation-toolkit-digistore/"
    )
    assert result["name"] == "Small Business AI and Automation Toolkit"
    assert result["description"] == "Automate repetitive admin and marketing tasks for a small business."
    assert result["image_url"] == ""


def test_vendor_product_details_uses_related_canonical_page_when_affiliate_page_is_thin(monkeypatch):
    requested = []

    class FakeResponse:
        status_code = 200
        headers = {}

        def __init__(self, text):
            self.text = text

        def raise_for_status(self):
            return None

    def fake_get(url, **kwargs):
        requested.append(url)
        if url.endswith("-digistore/"):
            return FakeResponse("<html><head><title>Marketplace listing</title></head><body></body></html>")
        return FakeResponse("""
        <html><head><script type="application/ld+json">
        {"@context":"https://schema.org","@type":"Product",
         "name":"Small Business AI and Automation Toolkit",
         "description":"A toolkit to automate small business workflows.",
         "image":"https://cdn.example/small-business-toolkit.png"}
        </script></head><body></body></html>
        """)

    monkeypatch.setattr(draft.httpx, "get", fake_get)
    result = draft._vendor_product_details(
        "https://marketplace.iotsystemsgrowth.com/small-business-ai-automation-toolkit-digistore/"
    )

    assert requested == [
        "https://marketplace.iotsystemsgrowth.com/small-business-ai-automation-toolkit-digistore/",
        "https://marketplace.iotsystemsgrowth.com/small-business-ai-automation-toolkit-clickbank/",
    ]
    assert result == {
        "name": "Small Business AI and Automation Toolkit",
        "description": "A toolkit to automate small business workflows.",
        "image_url": "https://cdn.example/small-business-toolkit.png",
    }


def test_known_toolkit_uses_user_confirmed_product_facts_and_artwork(monkeypatch):
    destination = (
        "https://marketplace.iotsystemsgrowth.com/"
        "small-business-ai-automation-toolkit-digistore/"
        "#aff=hapeenooo333cff7&cam="
    )
    monkeypatch.setattr(
        draft,
        "_vendor_product_details",
        lambda url: (_ for _ in ()).throw(
            RuntimeError("Vendor landing page does not expose static metadata")
        ),
    )
    monkeypatch.setattr(draft, "_generate_copy", _fake_copy)

    result = draft.build_external_pinterest_draft(
        affiliate_url=destination,
        niche="AI tools and business automation",
        language="English",
    )

    assert result["success"] is True
    assert result["product"]["name"] == "Small Business AI & Automation Toolkit"
    assert result["draft"]["destination_url"] == destination
    assert result["draft"]["image_url"] == (
        "https://iotsystemsgrowth.com/wp-content/uploads/2026/08/AI_Readiness_Preview.png"
    )
    assert result["draft"]["image_source"] == "user_confirmed_vendor_artwork"
    assert result["requires_explicit_approval"] is True
    assert result["published"] is False
