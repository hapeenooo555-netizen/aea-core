from app.services.prompt_intelligence import build_intelligence_context, detect_domain


def test_detects_video_from_plain_language_without_professional_terms():
    assert detect_domain("Nataka video ya kutangaza duka langu la nguo") == "video"


def test_detects_graphic_design_from_simple_customer_request():
    assert detect_domain("Nitengezee poster ya biashara yangu") == "graphic_design"


def test_preserves_verbatim_intent_and_explicit_constraints():
    prompt = "Tengeneza tangazo la duka langu, kwa Kiswahili, bila kubadilisha bei."
    context = build_intelligence_context(
        prompt=prompt,
        content_type="custom",
        niche="retail",
        audience="Wateja wa Dar es Salaam",
        objective="leads",
        language="Swahili",
        tone="professional",
        platforms=["facebook", "instagram"],
    )
    assert context["customer_intent_verbatim"] == prompt
    assert context["explicit_constraints"]["audience"] == "Wateja wa Dar es Salaam"
    assert context["explicit_constraints"]["platforms"] == ["facebook", "instagram"]
    assert "Do not reject" in context["intent_preservation_policy"]


def test_missing_information_policy_does_not_allow_invented_facts():
    context = build_intelligence_context(
        prompt="Tengeneza poster ya biashara yangu",
        content_type="custom",
        niche="Graphic Design",
        audience=None,
        objective="reach",
        language="Swahili",
        tone="professional",
        platforms=["facebook"],
    )
    assert "Never invent prices" in context["missing_information_policy"]


def test_domain_profiles_include_quality_and_professional_roles():
    context = build_intelligence_context(
        prompt="Design a professional logo and brand identity",
        content_type="custom",
        niche="Graphic Design",
        audience=None,
        objective="reach",
        language="English",
        tone="professional",
        platforms=["instagram"],
    )
    assert context["domain"] == "graphic_design"
    assert "graphic_designer" in context["specialist_roles"]
    assert "contrast" in context["quality_standards"]


def test_prompt_hash_is_stable_and_configuration_sensitive():
    from app.routers.content_studio import _prompt_hash

    first = _prompt_hash("You are AEA.", {"temperature": 0.2})
    same = _prompt_hash("You are AEA.", {"temperature": 0.2})
    changed = _prompt_hash("You are AEA.", {"temperature": 0.7})
    assert first == same
    assert first != changed
    assert len(first) == 64


def test_domain_detection_supports_swahi_plain_language():
    assert detect_domain("Nahitaji bango la duka langu") == "graphic_design"
    assert detect_domain("Nisaidie kuandika ripoti ya biashara") == "business_documents"
    assert detect_domain("Nataka kampeni ya kutafuta wateja") == "marketing"


def test_prompt_version_integrity_accepts_unchanged_persisted_content():
    from app.routers.content_studio import _prompt_hash, _prompt_version_integrity_valid

    version = {
        "system_prompt": "You are AEA.",
        "configuration": {"temperature": 0.2},
    }
    version["content_sha256"] = _prompt_hash(
        version["system_prompt"], version["configuration"]
    )
    assert _prompt_version_integrity_valid(version)


def test_prompt_version_integrity_rejects_modified_persisted_content():
    from app.routers.content_studio import _prompt_hash, _prompt_version_integrity_valid

    version = {
        "system_prompt": "You are AEA.",
        "configuration": {"temperature": 0.2},
    }
    version["content_sha256"] = _prompt_hash(
        version["system_prompt"], version["configuration"]
    )
    version["system_prompt"] = "You are a different assistant."
    assert not _prompt_version_integrity_valid(version)
