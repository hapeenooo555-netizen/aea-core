"""Deterministic, Unicode-capable text overlay renderer for AEA designs."""
from __future__ import annotations

import io
import re
import unicodedata
from dataclasses import dataclass

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


def _fit_lines(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
    max_lines: int,
) -> list[str]:
    """Wrap copy and visibly mark any text that cannot fit in its allocated area."""
    lines = _wrap(draw, text, font, max_width)
    if len(lines) <= max_lines:
        return lines
    visible = lines[:max_lines]
    last = visible[-1].rstrip()
    while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
        last = last[:-1].rstrip()
    visible[-1] = (last + "…") if last else "…"
    return visible


def _fit_single_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    font: ImageFont.ImageFont,
    max_width: int,
) -> str:
    label = text.strip()
    while label and draw.textbbox((0, 0), label, font=font)[2] > max_width:
        label = label[:-1].rstrip()
    if label != text.strip() and label:
        while label and draw.textbbox((0, 0), label + "…", font=font)[2] > max_width:
            label = label[:-1].rstrip()
        label += "…"
    return label or "…"


def render_design_copy(image_bytes: bytes, copy: DesignCopy) -> tuple[bytes, int, int]:
    """Overlay exact user-approved copy with a mobile-first, high-contrast hierarchy."""
    if not image_bytes:
        raise ValueError("Base image is empty")

    def clean_copy(value: str, limit: int) -> str:
        # Remove unresolved template tokens and pictographs unsupported by the
        # installed text fonts; preserve Kiswahili, punctuation and Latin text.
        value = re.sub(r"\\[[A-Z0-9_ -]*PLACEHOLDER[A-Z0-9_ -]*\\]", "", value or "", flags=re.IGNORECASE)
        value = "".join(ch for ch in value if unicodedata.category(ch) != "So")
        value = re.sub(r"\\s+", " ", value).strip()
        return value[:limit]

    title = clean_copy(copy.title, 180)
    body = clean_copy(copy.body, 500)
    cta = clean_copy(copy.cta, 80) or "Jifunze zaidi"
    brand = clean_copy(copy.brand, 80)
    if not title:
        raise ValueError("A headline is required for text rendering")

    with Image.open(io.BytesIO(image_bytes)) as source:
        canvas = source.convert("RGBA")
    width, height = canvas.size
    scale = max(min(width / 1080, height / 1350), 0.60)
    margin = int(width * 0.075)
    max_text_width = width - 2 * margin

    # A taller copy panel gives the headline, supporting message and CTA
    # enough room to remain legible on a phone-sized 4:5 or 9:16 canvas.
    panel_height = int(height * (0.48 if body else 0.39))
    panel_top = height - panel_height - int(height * 0.035)
    panel_left = margin // 2
    panel_right = width - margin // 2
    panel_bottom = height - margin // 2

    overlay = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    panel_draw = ImageDraw.Draw(overlay)
    panel_draw.rounded_rectangle(
        (panel_left, panel_top, panel_right, panel_bottom),
        radius=int(26 * scale),
        fill=(5, 16, 34, 250),
    )
    accent = _color(copy.accent, "#10B981")
    panel_draw.rounded_rectangle(
        (panel_left, panel_top, panel_left + max(7, int(9 * scale)), panel_bottom),
        radius=int(5 * scale),
        fill=(*accent, 255),
    )
    canvas = Image.alpha_composite(canvas, overlay)
    draw = ImageDraw.Draw(canvas)
    text_x = margin
    inner_top = panel_top + int(28 * scale)
    inner_bottom = panel_bottom - int(24 * scale)

    brand_size = max(15, int(20 * scale))
    title_size = max(30, int(72 * scale))
    body_size = max(19, int(31 * scale))
    cta_size = max(18, int(26 * scale))
    brand_font = _font(brand_size, bold=True)
    title_font = _font(title_size, bold=True)
    body_font = _font(body_size)
    cta_font = _font(cta_size, bold=True)

    brand_lines = _fit_lines(draw, brand, brand_font, max_text_width, 1) if brand else []
    title_lines = _fit_lines(draw, title, title_font, max_text_width, 3)
    body_lines = _fit_lines(draw, body, body_font, max_text_width, 3) if body else []
    cta_label = _fit_single_line(draw, cta, cta_font, max_text_width - int(48 * scale))

    # Reserve the full vertical budget before painting so copy never runs
    # underneath the CTA or beyond the opaque panel.
    brand_height = int(brand_size * 1.35) if brand_lines else 0
    title_line_height = int(title_size * 1.16)
    body_line_height = int(body_size * 1.42)
    cta_height = int(50 * scale)
    gaps = int(14 * scale) + int(10 * scale) + int(14 * scale)
    required = (
        brand_height
        + (title_line_height * len(title_lines))
        + (body_line_height * len(body_lines))
        + cta_height
        + gaps
    )
    available = inner_bottom - inner_top
    while required > available and (len(body_lines) > 1 or title_size > max(25, int(48 * scale))):
        if len(body_lines) > 1:
            body_lines = body_lines[:-1]
            if body_lines:
                last = body_lines[-1].rstrip(" .")
                body_lines[-1] = last + "…"
        else:
            title_size = max(int(48 * scale), title_size - 2)
            title_font = _font(title_size, bold=True)
            title_lines = _fit_lines(draw, title, title_font, max_text_width, 3)
            title_line_height = int(title_size * 1.16)
        required = (
            brand_height
            + (title_line_height * len(title_lines))
            + (body_line_height * len(body_lines))
            + cta_height
            + gaps
        )
        if not body_lines and title_size <= max(25, int(48 * scale)):
            break

    y = inner_top
    if brand_lines:
        draw.text((text_x, y), brand_lines[0], font=brand_font, fill=(205, 224, 235, 255))
        y += brand_height + int(14 * scale)

    for line in title_lines:
        draw.text((text_x, y), line, font=title_font, fill=(255, 255, 255, 255))
        y += title_line_height

    if body_lines:
        y += int(10 * scale)
        for line in body_lines:
            draw.text((text_x, y), line, font=body_font, fill=(231, 239, 245, 255))
            y += body_line_height

    pill_h = cta_height
    pill_w = min(max_text_width, draw.textbbox((0, 0), cta_label, font=cta_font)[2] + int(48 * scale))
    pill_y = min(inner_bottom - pill_h, max(y + int(12 * scale), inner_bottom - pill_h))
    draw.rounded_rectangle(
        (text_x, pill_y, text_x + pill_w, pill_y + pill_h),
        radius=pill_h // 2,
        fill=(*accent, 255),
    )
    draw.text(
        (text_x + int(24 * scale), pill_y + int(8 * scale)),
        cta_label,
        font=cta_font,
        fill=(7, 25, 31, 255),
    )

    output = io.BytesIO()
    canvas.convert("RGB").save(output, format="PNG", optimize=True)
    return output.getvalue(), width, height
