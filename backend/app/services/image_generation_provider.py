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
    """Resolve an enabled image provider or fail closed.

    AEA's model registry currently stores provider/model metadata, not secrets.
    Until a concrete adapter is registered and its deployment secret exists,
    callers must receive an explicit unavailable result rather than a mock
    image or an invented asset URL.
    """
    raise ImageProviderNotConfigured(
        "No image-generation provider is configured for AEA Core."
    )
