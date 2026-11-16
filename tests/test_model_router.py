from __future__ import annotations

import pytest

from app.services.model_router import ModelSpec, load_catalog, route_model


def test_catalog_has_extensible_provider_entries():
    models = load_catalog()
    assert {m.provider for m in models} >= {"runway", "pika", "xai", "wan"}
    assert all(m.capabilities for m in models)


def test_custom_provider_can_be_added_without_changing_router_code():
    models = load_catalog('[{"provider":"future-ai","model_id":"video-v2","capabilities":["text_to_video"],"enabled":true,"adapter":"future_ai"}]')
    selected = route_model(models, "text_to_video", quality="balanced")
    assert selected.provider == "future-ai"
    assert selected.model_id == "video-v2"


def test_router_respects_capability_and_enabled_flag():
    models = [
        ModelSpec("provider-a", "disabled", "Disabled", ("text_to_video",), enabled=False, adapter="a"),
        ModelSpec("provider-b", "image", "Image", ("image_generation",), enabled=True, adapter="b"),
    ]
    with pytest.raises(LookupError):
        route_model(models, "text_to_video")


def test_router_respects_cost_ceiling():
    models = [
        ModelSpec("premium", "p1", "Premium", ("text_to_video",), "premium", 0.10, enabled=True, adapter="p"),
        ModelSpec("budget", "b1", "Budget", ("text_to_video",), "economy", 0.01, enabled=True, adapter="b"),
    ]
    assert route_model(models, "text_to_video", max_cost_per_unit=0.02).provider == "budget"


def test_catalog_rejects_negative_cost_and_invalid_shape():
    with pytest.raises(ValueError):
        load_catalog('[{"provider":"x","model_id":"m","capabilities":["video"],"cost_per_unit":-1}]')
    with pytest.raises(ValueError):
        load_catalog('{"provider":"x"}')
