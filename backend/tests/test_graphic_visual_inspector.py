from __future__ import annotations

import io

import pytest
from PIL import Image, ImageDraw

from app.services import graphic_visual_inspector as inspector


def _background_bytes() -> bytes:
    image = Image.new("RGB", (1024, 1024), "#163b4a")
    draw = ImageDraw.Draw(image)
    draw.rectangle((80, 80, 500, 500), fill="#19a58a")
    draw.ellipse((520, 180, 940, 600), fill="#e2b85b")
    draw.polygon([(100, 900), (500, 570), (900, 900)], fill="#315a83")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _ocr_result(words=(), confidences=()):
    return {
        "text": list(words),
        "conf": list(confidences),
    }


def test_inspector_accepts_valid_background_without_detected_text(monkeypatch):
    monkeypatch.setattr(
        inspector.pytesseract,
        "image_to_data",
        lambda *_args, **_kwargs: _ocr_result(),
    )
    result = inspector.inspect_generated_background(_background_bytes())
    assert result.accepted is True
    assert result.reason == "passed"
    assert result.width == 1024
    assert result.height == 1024
    assert "contrast_stddev" in result.quality_metrics


def test_inspector_rejects_confident_readable_unwanted_text(monkeypatch):
    monkeypatch.setattr(
        inspector.pytesseract,
        "image_to_data",
        lambda *_args, **_kwargs: _ocr_result(
            ("SALE", "x", "2026"), ("96", "99", "94")
        ),
    )
    result = inspector.inspect_generated_background(_background_bytes())
    assert result.accepted is False
    assert result.reason == "unwanted_text_detected"
    assert result.detected_text == ("SALE",)


def test_inspector_ignores_low_confidence_ocr_noise(monkeypatch):
    monkeypatch.setattr(
        inspector.pytesseract,
        "image_to_data",
        lambda *_args, **_kwargs: _ocr_result(
            ("S", "aB", "l", "??"), ("98", "42", "91", "99")
        ),
    )
    result = inspector.inspect_generated_background(_background_bytes())
    assert result.accepted is True
    assert result.detected_text == ()


def test_inspector_rejects_corrupt_image():
    with pytest.raises(ValueError, match="valid readable image"):
        inspector.inspect_generated_background(b"not-an-image")


def test_inspector_does_not_reject_composition_using_saliency_heuristics(monkeypatch):
    monkeypatch.setattr(
        inspector.pytesseract,
        "image_to_data",
        lambda *_args, **_kwargs: _ocr_result(),
    )
    result = inspector.inspect_generated_background(_background_bytes())
    assert result.accepted is True
    # Metrics remain available for diagnostics and future model calibration.
    assert "saliency_center_x" in result.quality_metrics
    assert "bottom_saliency_ratio" in result.quality_metrics
