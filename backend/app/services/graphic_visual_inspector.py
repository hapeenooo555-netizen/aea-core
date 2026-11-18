"""Pre-composition quality gate for generated graphic backgrounds.

OCR is deliberately local (Tesseract); no image is sent to a third-party
inspection API. If OCR is unavailable or the image cannot be inspected, the
gate fails closed rather than claiming the background is clean.
"""
from __future__ import annotations

import io
import re
from dataclasses import asdict, dataclass

from PIL import Image, UnidentifiedImageError
import pytesseract
from pytesseract import Output


@dataclass(frozen=True)
class VisualInspection:
    accepted: bool
    reason: str
    width: int
    height: int
    detected_text: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return asdict(self)


def inspect_generated_background(image_bytes: bytes) -> VisualInspection:
    if not image_bytes:
        raise ValueError("Generated background is empty")
    try:
        with Image.open(io.BytesIO(image_bytes)) as source:
            source.verify()
        with Image.open(io.BytesIO(image_bytes)) as source:
            image = source.convert("RGB")
            width, height = image.size
            if width < 512 or height < 512:
                return VisualInspection(False, "image_too_small", width, height)
            if width > 8192 or height > 8192:
                return VisualInspection(False, "image_dimensions_exceed_limit", width, height)
            try:
                data = pytesseract.image_to_data(
                    image, output_type=Output.DICT, config="--psm 11"
                )
            except pytesseract.TesseractNotFoundError as exc:
                raise RuntimeError("Local OCR engine is unavailable; inspection cannot pass") from exc
            found: list[str] = []
            for text, confidence in zip(data.get("text", []), data.get("conf", [])):
                candidate = str(text).strip()
                try:
                    score = float(confidence)
                except (TypeError, ValueError):
                    continue
                if score >= 60 and re.search(r"[A-Za-z0-9]{2,}", candidate):
                    found.append(candidate[:40])
            if found:
                return VisualInspection(
                    False, "unwanted_text_detected", width, height,
                    tuple(dict.fromkeys(found))[:12],
                )
            return VisualInspection(True, "passed", width, height)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Generated background is not a valid readable image") from exc
