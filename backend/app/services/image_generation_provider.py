"""Provider-neutral image generation contract for AEA Core.

Concrete providers must implement this interface and keep credentials server-side.
This module deliberately does not pretend that a provider is configured.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class GeneratedImage:
    content: bytes
    mime_type: str
    width: int
    height: int
    provider: str
    model: str
    provider_metadata: dict[str, Any]


class ImageGenerationProvider(Protocol):
    name: str

    async def generate(
        self,
        *,
        prompt: str,
        aspect_ratio: str,
        model: str | None = None,
    ) -> GeneratedImage:
        """Generate and return image bytes plus verified media metadata."""
        ...


class ImageProviderNotConfigured(RuntimeError):
    """Raised when no enabled, credentialed image provider is available."""


def get_image_generation_provider() -> ImageGenerationProvider:
    """Resolve fal.ai using server-side credentials and the atomic budget ledger."""
    import os

    from app.database import get_supabase_image_budget_client
    from app.services.fal_image_generation_provider import FalImageGenerationProvider
    from app.services.image_generation_budget import SupabaseImageBudget

    if os.getenv("AEA_IMAGE_PROVIDER", "fal").lower() != "fal":
        raise ImageProviderNotConfigured("Configured image provider is not supported")
    client = get_supabase_image_budget_client()
    if client is None:
        raise ImageProviderNotConfigured(
            "Supabase service-role configuration is required for image budget enforcement"
        )
    return FalImageGenerationProvider(SupabaseImageBudget(client))
