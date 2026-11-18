import pytest

from app.services.image_generation_provider import (
    GeneratedImage,
    ImageProviderNotConfigured,
    get_image_generation_provider,
)


def test_image_provider_contract_returns_unavailable_until_adapter_is_configured():
    with pytest.raises(ImageProviderNotConfigured):
        get_image_generation_provider()


def test_generated_image_contract_carries_bytes_and_verified_metadata():
    image = GeneratedImage(
        content=b"image-bytes",
        mime_type="image/png",
        width=1024,
        height=1024,
        provider="test-provider",
        model="test-image-model",
        provider_metadata={"request_id": "test-123"},
    )
    assert image.content == b"image-bytes"
    assert image.mime_type == "image/png"
    assert (image.width, image.height) == (1024, 1024)
    assert image.provider == "test-provider"
    assert image.model == "test-image-model"
