"""Pre-composition quality gate for generated graphic backgrounds.

OCR is deliberately local (Tesseract); no image is sent to a third-party
inspection API. If OCR is unavailable or the image cannot be inspected, the
gate fails closed rather than claiming the background is clean.
"""
from __future__ import annotations

import io
import re
import math
from dataclasses import asdict, dataclass

from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError
import pytesseract
from pytesseract import Output


@dataclass(frozen=True)
class VisualInspection:
    accepted: bool
    reason: str
    width: int
    height: int
    detected_text: tuple[str, ...] = ()
    quality_metrics: dict | None = None

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
            metrics = _measure_visual_quality(image)
            quality_failures = []
            if metrics["contrast_stddev"] < 12:
                quality_failures.append("low_contrast")
            if metrics["sharpness_score"] < 1.2:
                quality_failures.append("soft_or_blurry")
            if metrics["clipped_pixel_ratio"] > 0.65:
                quality_failures.append("exposure_clipping")
            if not (0.08 <= metrics["saliency_center_x"] <= 0.92 and 0.08 <= metrics["saliency_center_y"] <= 0.92):
                quality_failures.append("poor_visual_balance")
            if metrics["bottom_saliency_ratio"] > 0.72:
                quality_failures.append("subject_may_conflict_with_text_panel")
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
                    tuple(dict.fromkeys(found))[:12], metrics,
                )
            if quality_failures:
                return VisualInspection(False, ",".join(quality_failures), width, height, (), metrics)
            return VisualInspection(True, "passed", width, height, (), metrics)
    except (UnidentifiedImageError, OSError) as exc:
        raise ValueError("Generated background is not a valid readable image") from exc



def _measure_visual_quality(image: Image.Image) -> dict:
    """Measure low-cost visual proxies; these are not semantic object detection."""
    sample = image.convert("RGB").resize((128, 128))
    gray = sample.convert("L")
    luminance = list(gray.getdata())
    mean = sum(luminance) / len(luminance)
    contrast = math.sqrt(sum((value - mean) ** 2 for value in luminance) / len(luminance))
    clipped = sum(1 for value in luminance if value <= 8 or value >= 247) / len(luminance)
    edge_stats = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES))
    sharpness = edge_stats.stddev[0]

    blurred = gray.filter(ImageFilter.GaussianBlur(radius=5))
    # Pixel-wise high-frequency residual acts as a lightweight saliency proxy.
    import PIL.ImageChops
    residual = PIL.ImageChops.difference(gray, blurred)
    weights = list(residual.getdata())
    total = sum(weights) or 1
    center_x = sum((index % 128 + 0.5) * weight for index, weight in enumerate(weights)) / total / 128
    center_y = sum((index // 128 + 0.5) * weight for index, weight in enumerate(weights)) / total / 128
    bottom_start = int(128 * 0.61)
    bottom_weight = sum(weights[row * 128 + col] for row in range(bottom_start, 128) for col in range(128))
    return {
        "contrast_stddev": round(contrast, 2),
        "sharpness_score": round(sharpness, 2),
        "clipped_pixel_ratio": round(clipped, 4),
        "saliency_center_x": round(center_x, 3),
        "saliency_center_y": round(center_y, 3),
        "bottom_saliency_ratio": round(bottom_weight / total, 4),
    }
