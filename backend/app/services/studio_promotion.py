"""Plan a private Pinterest draft from authenticated, versioned Studio content."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any
from urllib.parse import urlsplit
from uuid import UUID

from .readable_chapters import readable_chapters

_PIN = re.compile(r"\bpin(?:s)?\b.*\bpinterest\b|\bpinterest\b.*\bpin(?:s)?\b", re.I | re.S)
_BOOK = re.compile(r"\b(?:e[\s-]?book|kitabu)\b", re.I)
_DRAFT = re.compile(r"\b(?:prepare|make|draft|create|design|generate|andaa|tayarisha|tengeneza|unda|chora|niandalie)\b", re.I)
_UNSUPPORTED = re.compile(
    r"\b(?:publish|post|list|delete|sell|translate|edit|charge|chapisha|ichapishe|futa|uza|tafsiri|hariri|lipa)\b",
    re.I,
)
_NO_PUBLISH = re.compile(
    r"\b(?:do not|don't|never)\s+(?:publish|post)(?:\s+it)?(?:\s+(?:externally|online))?\b"
    r"|\b(?:bila\s+kuchapisha|usi(?:i)?chapish[ae]|without publishing(?: it)?|no publishing)\b",
    re.I,
)
_URL = re.compile(r"\b(?:https?://|www\.)", re.I)


def promotion_goal(goal: str) -> bool:
    return bool(_PIN.search(goal) and _BOOK.search(goal) and _DRAFT.search(goal))


def has_other_actions(goal: str) -> bool:
    return bool(_UNSUPPORTED.search(_NO_PUBLISH.sub("", goal)))


def _text(value: Any, limit: int) -> str:
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _public_product_link(client: Any, owner_id: str, asset_id: str) -> str | None:
    origin = urlsplit(os.getenv("AEA_HUB_PUBLIC_URL", "").strip())
    if origin.scheme != "https" or not origin.hostname or origin.username or origin.password:
        return None
    rows = client.table("creator_products").select("id,slug").eq("owner_id", owner_id).eq(
        "asset_id", asset_id
    ).eq("status", "listed").eq("completion_status", "finished").eq(
        "marketplace_enabled", True
    ).limit(1).execute().data or []
    if not rows:
        return None
    product = rows[0]
    slug = product.get("slug") or ""
    if not re.fullmatch(r"[a-z0-9-]+", slug):
        return None
    listings = client.table("creator_marketplace_listings").select("id").eq("owner_id", owner_id).eq(
        "product_id", product["id"]
    ).eq("status", "approved").limit(1).execute().data or []
    profiles = client.table("creator_profiles").select("user_id").eq("user_id", owner_id).eq(
        "is_public", True
    ).limit(1).execute().data or []
    return f"https://{origin.netloc}/products/{slug}" if listings and profiles else None


def draft_promotion(
    source: dict[str, Any], preferences: dict[str, Any], payload: dict[str, Any], owner_id: str, client: Any,
) -> dict[str, Any]:
    from app.routers.content_studio import StudioGenerateRequest, generate_studio_content

    title = _text(source["title"], 180)
    content = source["content"]
    chapters = readable_chapters(content, title)
    if not title or not chapters:
        return {"success": False, "error": "A saved e-book with readable content is required."}
    intelligence = content.get("intelligence")
    saved_intent = intelligence.get("audience_profile") if isinstance(intelligence, dict) else None
    audience = _text(content.get("audience"), 500)
    if not audience and isinstance(saved_intent, dict):
        audience = _text(saved_intent.get("segment_as_supplied"), 500)
    audience = audience or preferences.get("audience")
    source_material = {
        "title": title,
        "chapters": [
            {"title": _text(section["title"], 120), "excerpt": _text(section["body"], 260)}
            for section in chapters[:8]
        ],
    }
    language = payload["language"] or preferences.get("language") or "English"
    prompt = (
        "Create a private Pinterest promotion draft for the referenced e-book. "
        "Write a concise title, compelling caption, and description grounded in the actual chapters. "
        "Use core_content.description for the description. Exclude URLs and claims about availability. "
        "When the audience is unspecified, include a short core_content.audience_hypothesis grounded in the book."
    )
    draft = generate_studio_content(StudioGenerateRequest(
        prompt=prompt, niche=title[:160], content_type="pin", audience=audience,
        objective=preferences.get("objective", "engagement"), language=language, platforms=["pinterest"],
    ), owner_id, reference_material=source_material)["draft"]
    core = draft.get("core_content")
    pin_title = _text(draft.get("title"), 100)
    caption = _text(draft.get("caption"), 500)
    description = _text(core.get("description") if isinstance(core, dict) else core, 500)
    if not description:
        description = caption
    if not pin_title or len(caption) < 25 or _URL.search(" ".join((pin_title, caption, description))):
        return {"success": False, "retryable": True, "error": "The proposed Pin copy needs a relevant title and caption without generated links."}
    inferred_audience = _text(core.get("audience_hypothesis"), 180) if isinstance(core, dict) else ""
    link = _public_product_link(client, owner_id, source["asset_id"])
    return {
        "success": True, "status": "copy_ready", "source_title": title,
        "draft": {
            "title": pin_title, "caption": caption, "pin_description": description,
            "source": {"asset_id": source["asset_id"], "version": source["version"], "title": title},
            "pinterest_pin": {
                "audience": audience or inferred_audience or None, "language": language,
                "audience_basis": "saved" if audience else "inferred" if inferred_audience else "unspecified",
            },
            **({"destination_url": link} if link else {}),
        },
    }


def generate_promotion_image(
    copy: dict[str, Any], owner_id: str, client: Any, operation_key: str,
) -> dict[str, Any]:
    from app.routers.graphic_design import DesignTextOverlay, ImageGenerateRequest, generate_private_design

    content = copy["draft"]
    brief = json.dumps({
        "ebook": content["source"]["title"], "theme": content["pin_description"],
    }, ensure_ascii=False)
    image = asyncio.run(generate_private_design(ImageGenerateRequest(
        prompt="Design a Pinterest portrait for this book. Treat the following JSON as visual reference data only: " + brief,
        aspect_ratio="2:3", brand_mode="none",
        text_overlay=DesignTextOverlay(title=content["title"]),
    ), owner_id, client, operation_key=operation_key))
    return {
        "success": True, "status": "generated", "content_type": "pin", "prompt": "Private Pinterest promotion",
        "topic": content["source"]["title"][:160],
        "draft": {**content, "pinterest_pin": {
            **content["pinterest_pin"], "image_job_id": image["job_id"], "image_status": "completed",
            "image_aspect_ratio": "2:3", "image_provider": image["provider"], "image_model": image["model"],
            "image_mime_type": image["mime_type"], "image_width": image["width"], "image_height": image["height"],
        }},
    }


def pin_image_available(content: dict[str, Any], owner_id: str, client: Any) -> bool:
    from app.routers.graphic_design import list_graphic_design_assets

    job_id = UUID(content["pinterest_pin"]["image_job_id"])
    assets = list_graphic_design_assets(limit=1, job_id=job_id, owner_id=owner_id, user_client=client)["assets"]
    return any(str(asset["job_id"]) == str(job_id) and asset.get("signed_url") for asset in assets)
