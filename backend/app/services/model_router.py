"""Extensible capability-based model catalogue and routing policy.

Providers are registered through configuration, not hard-coded into workflows.
Adding a provider/model requires a catalogue entry and (when execution is enabled)
an adapter implementing the provider contract. Secrets remain in environment vars.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ModelSpec:
    provider: str
    model_id: str
    display_name: str
    capabilities: tuple[str, ...]
    quality_tier: str = "standard"
    cost_per_unit: float | None = None
    cost_unit: str = "second"
    enabled: bool = True
    adapter: str | None = None

    def public_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider, "model_id": self.model_id,
            "display_name": self.display_name, "capabilities": list(self.capabilities),
            "quality_tier": self.quality_tier, "cost_per_unit": self.cost_per_unit,
            "cost_unit": self.cost_unit, "enabled": self.enabled,
            "ready_for_execution": bool(self.adapter),
        }


DEFAULT_CATALOG = [
    {"provider":"runway","model_id":"gen4.5","display_name":"Runway Gen-4.5","capabilities":["text_to_video","image_to_video","cinematic_storytelling"],"quality_tier":"premium","cost_per_unit":0.12,"cost_unit":"second","enabled":False,"adapter":None},
    {"provider":"pika","model_id":"h3-flex","display_name":"Pika H3 Flex","capabilities":["text_to_video","image_to_video","audio_video","social_video"],"quality_tier":"premium","cost_per_unit":None,"cost_unit":"second","enabled":False,"adapter":None},
    {"provider":"xai","model_id":"grok-imagine-video-lite","display_name":"Grok Imagine Lite","capabilities":["text_to_video","image_to_video","social_video"],"quality_tier":"economy","cost_per_unit":0.02,"cost_unit":"second","enabled":False,"adapter":None},
    {"provider":"wan","model_id":"wan-open-weight","display_name":"Wan open-weight family","capabilities":["text_to_video","image_to_video","self_hosted"],"quality_tier":"configurable","cost_per_unit":None,"cost_unit":"second","enabled":False,"adapter":None},
]


def load_catalog(raw: str | None = None) -> list[ModelSpec]:
    """Load defaults plus optional JSON overrides/additions from env/config."""
    entries = list(DEFAULT_CATALOG)
    supplied = raw if raw is not None else os.getenv("AEA_MODEL_CATALOG_JSON", "")
    if supplied.strip():
        extra = json.loads(supplied)
        if not isinstance(extra, list):
            raise ValueError("AEA_MODEL_CATALOG_JSON must be a JSON array")
        entries.extend(extra)
    models: dict[tuple[str, str], ModelSpec] = {}
    for item in entries:
        spec = ModelSpec(
            provider=str(item["provider"]).strip().lower(),
            model_id=str(item["model_id"]).strip(),
            display_name=str(item.get("display_name") or item["model_id"]),
            capabilities=tuple(sorted(set(item.get("capabilities") or []))),
            quality_tier=str(item.get("quality_tier") or "standard"),
            cost_per_unit=float(item["cost_per_unit"]) if item.get("cost_per_unit") is not None else None,
            cost_unit=str(item.get("cost_unit") or "second"),
            enabled=bool(item.get("enabled", True)),
            adapter=item.get("adapter"),
        )
        if not spec.provider or not spec.model_id or not spec.capabilities:
            raise ValueError("Each model needs provider, model_id and at least one capability")
        if spec.cost_per_unit is not None and spec.cost_per_unit < 0:
            raise ValueError("Model cost cannot be negative")
        models[(spec.provider, spec.model_id)] = spec
    return list(models.values())


def route_model(
    models: list[ModelSpec], capability: str, quality: str = "balanced",
    max_cost_per_unit: float | None = None, preferred_provider: str | None = None,
) -> ModelSpec:
    """Choose an enabled model by capability, quality and optional cost ceiling."""
    candidates = [
        m for m in models if m.enabled and capability in m.capabilities
        and (max_cost_per_unit is None or (m.cost_per_unit is not None and m.cost_per_unit <= max_cost_per_unit))
    ]
    if not candidates:
        raise LookupError(f"No enabled model supports capability: {capability}")
    quality_order = {
        "economy": {"economy": 0, "standard": 1, "premium": 2, "configurable": 3},
        "balanced": {"standard": 0, "economy": 1, "premium": 2, "configurable": 3},
        "premium": {"premium": 0, "standard": 1, "configurable": 2, "economy": 3},
    }
    ranks = quality_order.get(quality, quality_order["balanced"])
    def score(m: ModelSpec) -> tuple[int, int, float]:
        preferred = 0 if preferred_provider and m.provider == preferred_provider else 1
        cost = m.cost_per_unit if m.cost_per_unit is not None else float("inf")
        return (preferred, ranks.get(m.quality_tier, 4), cost)
    return sorted(candidates, key=score)[0]


def get_model_catalog() -> list[dict[str, Any]]:
    return [model.public_dict() for model in load_catalog()]
