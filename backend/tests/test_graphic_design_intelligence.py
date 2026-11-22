from __future__ import annotations

import json

import pytest

from app.services import graphic_design_intelligence as intelligence


class FakeLLM:
    name = "groq"

    def __init__(self, payload):
        self.payload = payload
        self.prompt = None
        self.system_prompt = None

    def generate(self, prompt, system_prompt=None):
        self.prompt = prompt
        self.system_prompt = system_prompt
        return {
            "success": True,
            "content": json.dumps(self.payload, ensure_ascii=False),
            "provider": "groq",
            "model": "test-model",
            "usage": {"total_tokens": 123},
        }


def _payload():
    return {
        "headline": "Biashara yako inaweza zaidi!",
        "body": "Tengeneza maudhui na boresha kazi zako za kidigitali.",
        "cta": "Gundua AEA leo",
        "brand": "",
        "accent": "#FF0000",
        "visual_prompt": "Premium business workspace, confident entrepreneur, subtle AI-inspired interface, cinematic navy background.",
        "design_rationale": "A clear benefit-led composition.",
        "audience": "Wamiliki wa biashara ndogo",
        "objective": "Brand awareness",
        "content_type": "advertisement",
    }


def test_aea_brand_request_uses_verified_brand_identity_and_brand_palette(monkeypatch):
    fake = FakeLLM(_payload())
    monkeypatch.setattr(intelligence, "get_llm_provider", lambda: fake)

    result = intelligence.compose_graphic_design(
        prompt="Nitengenezee poster inayoelezea AEA Affiliate Hub",
        aspect_ratio="4:5",
    )

    brief = json.loads(fake.prompt)
    assert brief["brand_request"] is True
    assert brief["brand_facts"]["brand_name"] == "AEA Affiliate Hub"
    assert "verified_capabilities" in brief["brand_facts"]
    assert result["brand_applied"] is True
    assert result["text_overlay"]["brand"] == "AEA Affiliate Hub"
    assert result["text_overlay"]["accent"] == "#10B981"
    assert "BACKGROUND ART ONLY" in result["visual_prompt"]
    assert "no text" in result["visual_prompt"].lower()


def test_non_aea_request_does_not_force_aea_branding(monkeypatch):
    fake = FakeLLM(_payload())
    monkeypatch.setattr(intelligence, "get_llm_provider", lambda: fake)

    result = intelligence.compose_graphic_design(
        prompt="Tengeneza poster ya duka la nguo",
        aspect_ratio="4:5",
        brand_mode="none",
    )

    assert result["brand_applied"] is False
    assert result["text_overlay"]["brand"] == ""
    assert result["text_overlay"]["accent"] == "#FF0000"


def test_aea_brand_mode_can_be_explicitly_selected(monkeypatch):
    fake = FakeLLM(_payload())
    monkeypatch.setattr(intelligence, "get_llm_provider", lambda: fake)

    result = intelligence.compose_graphic_design(
        prompt="Tengeneza infographic kuhusu elimu ya biashara",
        aspect_ratio="1:1",
        brand_mode="aea",
    )

    assert result["brand_applied"] is True


def test_missing_headline_fails_closed(monkeypatch):
    payload = _payload()
    payload["headline"] = ""
    monkeypatch.setattr(intelligence, "get_llm_provider", lambda: FakeLLM(payload))

    with pytest.raises(RuntimeError, match="invalid headline"):
        intelligence.compose_graphic_design(
            prompt="Tengeneza tangazo",
            aspect_ratio="4:5",
        )


def test_design_intelligence_requires_real_llm_provider(monkeypatch):
    class MockProvider:
        name = "mock"

    monkeypatch.setattr(intelligence, "get_llm_provider", lambda: MockProvider())
    with pytest.raises(RuntimeError, match="production LLM"):
        intelligence.compose_graphic_design(
            prompt="Tengeneza poster ya biashara",
            aspect_ratio="4:5",
        )
