"""Deterministic, Unicode-capable text overlay renderer for AEA designs."""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


@dataclass(frozen=True)
class DesignCopy:
    title: str
    body: str = ""
    cta: str = ""
    brand: str = ""
    accent: str = "#10B981"


def _color(value: str, fallback: str) -> tuple[int, int, int]:
    if not isinstance(value, str) or not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
        value = fallback
    return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    candidates = (
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf"),
    )
    for path in candidates:
        try:
            return ImageFont.truetype(path, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    for paragraph in text.splitlines() or [""]:
        words = paragraph.split()
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if current and draw.textbbox((0, 0), candidate, font=font)[2] > max_width:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current:
            lines.append(current)
    return lines


def render_design_copy(image_bytes: bytes, copy: DesignCopy) -> tuple[bytes, int, int]:
    """Overlay exact user-approved copy onto a generated visual; return PNG bytes."""
    if not image_bytes:
        raise ValueError("Base image is empty")
    title = (copy.title or "").strip()[:180]
    body = (copy.body or "").strip()[:500]
    cta = (copy.cta or "").strip()[:80]
    brand = (copy.brand or "").strip()[:80]
    if not title:
        raise ValueError("A headline is required for text rendering")

    with Image.open(io.BytesIO(image_bytes)) as source:
        canvas = source.convert("RGBA")
    width, height = canvas.size
    scale = max(width / 1080, height / 1350, 0.65)
    margin = int(width * 0.075)
    panel_height = int(height * (0.39 if body else 0.30))
    panel_top = height - panel_height - int(height * 0.045)
    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    panel_box = (margin // 2, panel_top, width - margin // 2, height - margin // 2)
    draw.rounded_rectangle(panel_box, radius=int(24 * scale), fill=(8, 18, 35, 224))
    accent = _color(copy.accent, "#10B981")
    draw.rounded_rectangle(
        (panel_box[0], panel_top, panel_box[0] + int(9 * scale), panel_box[3]),
        radius=int(5 * scale), fill=(*accent, 255),
    )
    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)
    text_x = margin
    max_text_width = width - 2 * margin

    if brand:
        brand_font = _font(max(16, int(19 * scale)), bold=True)
        draw.text((text_x, panel_top + int(24 * scale)), brand, font=brand_font, fill=(205, 224, 235, 255))
    title_size = int(58 * scale)
    min_title_size = int(34 * scale)
    title_font = _font(title_size, bold=True)
    title_lines = _wrap(draw, title, title_font, max_text_width)
    while title_size > min_title_size and len(title_lines) > 3:
        title_size -= 2
        title_font = _font(title_size, bold=True)
        title_lines = _wrap(draw, title, title_font, max_text_width)
    y = panel_top + (int(58 * scale) if brand else int(28 * scale))
    line_gap = int(title_size * 1.18)
    for line in title_lines[:3]:
        draw.text((text_x, y), line, font=title_font, fill=(255, 255, 255, 255), stroke_width=0)
        y += line_gap

    if body:
        body_font = _font(int(25 * scale))
        body_lines = _wrap(draw, body, body_font, max_text_width)
        y += int(8 * scale)
        for line in body_lines[:4]:
            draw.text((text_x, y), line, font=body_font, fill=(231, 239, 245, 255))
            y += int(34 * scale)

    if cta:
        cta_font = _font(int(23 * scale), bold=True)
        label = cta
        box = draw.textbbox((0, 0), label, font=cta_font)
        pill_w = min(max_text_width, box[2] + int(52 * scale))
        pill_h = int(48 * scale)
        pill_y = min(height - margin - pill_h, max(y + int(12 * scale), panel_top + panel_height - pill_h - int(20 * scale)))
        draw.rounded_rectangle((text_x, pill_y, text_x + pill_w, pill_y + pill_h), radius=pill_h // 2, fill=(*accent, 255))
        draw.text((text_x + int(24 * scale), pill_y + int(8 * scale)), label, font=cta_font, fill=(7, 25, 31, 255))

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue(), width, height
