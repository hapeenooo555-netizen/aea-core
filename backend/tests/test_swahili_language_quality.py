from app.services.swahili_language_quality import (
    apply_swahili_language_quality,
    normalize_swahili_text,
)


def test_replaces_known_awkward_swahilized_english_and_design_terms():
    text = "Specifikesheni ya poster na creative direction pamoja na layout."
    result = normalize_swahili_text(text)
    assert "specifikesheni" not in result.casefold()
    assert "mwelekeo wa ubunifu" in result
    assert "mpangilio" in result


def test_keeps_common_social_media_terms_when_they_are_natural():
    text = "Weka post yako, tuma link kwenye inbox na jibu comment."
    assert normalize_swahili_text(text) == text


def test_recursively_checks_nested_draft_without_changing_non_text_values():
    draft = {
        "title": "Design specification ya AEA",
        "sections": [{"heading": "Creative direction", "body": "Angalia layout."}],
        "count": 3,
        "enabled": True,
    }
    result = apply_swahili_language_quality(draft)
    assert result["title"] == "mwongozo wa ubunifu ya AEA"
    assert result["sections"][0]["heading"] == "mwelekeo wa ubunifu"
    assert result["sections"][0]["body"] == "Angalia mpangilio."
    assert result["count"] == 3
    assert result["enabled"] is True
