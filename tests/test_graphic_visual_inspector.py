from io import BytesIO

from PIL import Image, ImageDraw

from app.services import graphic_visual_inspector as inspector


def _png():
    stream = BytesIO()
    image = Image.new("RGB", (800, 1000), (120, 150, 180))
    draw = ImageDraw.Draw(image)
    for x in range(0, 800, 8):
        draw.line((x, 0, x, 1000), fill=(40 + (x % 160), 90, 170), width=3)
    image.save(stream, format="PNG")
    return stream.getvalue()


def test_visual_inspector_accepts_valid_background_without_detected_text(monkeypatch):
    monkeypatch.setattr(
        inspector.pytesseract,
        "image_to_data",
        lambda *args, **kwargs: {"text": ["", " "], "conf": ["-1", "-1"]},
    )
    result = inspector.inspect_generated_background(_png())
    assert result.accepted is True
    assert result.reason == "passed"


def test_visual_inspector_rejects_detected_text(monkeypatch):
    monkeypatch.setattr(
        inspector.pytesseract,
        "image_to_data",
        lambda *args, **kwargs: {"text": ["KARIOKOR"], "conf": ["91"]},
    )
    result = inspector.inspect_generated_background(_png())
    assert result.accepted is False
    assert result.reason == "unwanted_text_detected"
    assert "KARIOKOR" in result.detected_text


def test_visual_inspector_rejects_invalid_image():
    try:
        inspector.inspect_generated_background(b"not-an-image")
    except ValueError as exc:
        assert "valid readable image" in str(exc)
    else:
        raise AssertionError("Invalid image must not pass inspection")



def test_visual_inspector_returns_quality_metrics(monkeypatch):
    monkeypatch.setattr(
        inspector.pytesseract,
        "image_to_data",
        lambda *args, **kwargs: {"text": [], "conf": []},
    )
    result = inspector.inspect_generated_background(_png())
    assert result.quality_metrics is not None
    assert "contrast_stddev" in result.quality_metrics
    assert "sharpness_score" in result.quality_metrics
    assert "bottom_saliency_ratio" in result.quality_metrics

def test_readable_ocr_tokens_ignores_low_confidence_and_glyph_noise():
    result = inspector._readable_ocr_tokens({
        "text": ["A", "12", "??", "KARIOKOR", "maybe"],
        "conf": ["99", "98", "99", "91", "42"],
    })
    assert result == ("KARIOKOR",)
