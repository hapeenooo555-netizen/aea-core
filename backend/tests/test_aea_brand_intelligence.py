from app.services.aea_brand_intelligence import get_aea_brand_context, get_aea_brand_profile


def test_brand_profile_preserves_homepage_positioning_and_taglines():
    profile = get_aea_brand_profile()
    identity = profile["identity"]
    assert identity["primary_tagline"] == "AI-assisted. Human-approved."
    assert identity["supporting_tagline"] == "Your workflow. Your approval."
    assert identity["brand_promise"] == "A clearer path from idea to approved content."
    assert "Pinterest" in identity["homepage_message"]
    assert "You review, decide, and stay in control." in identity["homepage_message"]


def test_brand_context_includes_human_approval_philosophy_and_visual_identity():
    context = get_aea_brand_context()
    assert any("user's judgement" in item for item in context["philosophy"])
    assert context["visual_identity"]["palette"]["deep_navy"] == "#0A1026"
    assert context["visual_identity"]["palette"]["gold"] == "#F5B83D"
    assert context["visual_identity"]["logo_asset"]["status"] == "user_supplied_reference_not_yet_packaged_for_runtime"


def test_brand_profile_is_returned_as_an_isolated_copy():
    first = get_aea_brand_profile()
    first["identity"]["name"] = "Changed"
    second = get_aea_brand_profile()
    assert second["identity"]["name"] == "AEA Affiliate Hub"
