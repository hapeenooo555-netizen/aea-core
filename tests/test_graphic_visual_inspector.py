from io import BytesIO

from PIL import Image

from app.services import graphic_visual_inspector as inspector


def _png():
    stream = BytesIO()
    Image.new("RGB", (800, 1000), (120, 150, 180)).save(stream, format="PNG")
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
