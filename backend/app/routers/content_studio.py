"""Authenticated AI Content Studio generation endpoint.

The endpoint returns an editable draft only. It never publishes or persists
external side effects. Production generation requires a configured real LLM.
"""
from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id
from app.services.llm_provider import get_llm_provider

router = APIRouter(prefix="/content/studio", tags=["content-studio"])

ContentType = Literal[
    "short_video", "long_video", "article", "blog", "seo", "social_post",
    "storytelling", "course", "ebook", "workbook", "newsletter", "podcast",
    "pin", "carousel", "infographic", "ad_copy", "product_description", "custom",
]


class StudioGenerateRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=12000)
    niche: str = Field(min_length=1, max_length=160)
    content_type: ContentType
    audience: str | None = Field(default=None, max_length=500)
    objective: Literal["reach", "engagement", "traffic", "leads", "sales", "education", "authority"] = "engagement"
    language: str = Field(default="English", min_length=2, max_length=40)
    tone: str = Field(default="helpful", min_length=2, max_length=80)
    platforms: list[Literal["pinterest", "facebook", "instagram", "whatsapp", "youtube"]] = Field(default_factory=list, max_length=5)
    model_config = ConfigDict(extra="forbid")


def _system_prompt() -> str:
    return (
        "You are AEA Content Studio, an expert content strategist and creator. "
        "Create useful, original, platform-aware content based on the supplied brief. "
        "Never fabricate research, trends, statistics, citations, product features, earnings or guarantees. "
        "If facts are uncertain, label them for human verification. Do not promise virality, views, likes or leads. "
        "Return ONLY valid JSON with keys: title, content_type, core_content, hooks (array of 3), "
        "caption, cta, hashtags (array), keywords (array), platform_variants (object), "
        "creative_direction, research_notes, verification_needed (array), primary_objective. "
        "Each platform variant should include format, caption and CTA when a platform is requested. "
        "For video, include script, scene_plan, voiceover and subtitle_text inside core_content. "
        "For ebooks/courses, include a table_of_contents and chapter_or_module_outline in core_content. "
        "For Pinterest, include pin_title, pin_description, image_concept and destination_placeholder. "
        "This is a draft for human review; do not publish."
    )


@router.post("/generate")
def generate_studio_draft(
    body: StudioGenerateRequest,
    owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Generate a structured, editable content draft for the authenticated user."""
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")

    provider = get_llm_provider()
    provider_name = str(getattr(provider, "name", "") or provider.__class__.__name__).lower()
    if provider_name in {"mockllmprovider", "mock"}:
        raise HTTPException(
            status_code=503,
            detail="AI Content Studio is not configured with a production LLM provider. Configure GROQ_API_KEY and LLM_PROVIDER=groq before generating live content.",
        )

    brief = {
        "prompt": body.prompt,
        "niche": body.niche,
        "content_type": body.content_type,
        "audience": body.audience,
        "objective": body.objective,
        "language": body.language,
        "tone": body.tone,
        "platforms": body.platforms,
    }
    try:
        response = provider.generate(
            prompt=json.dumps(brief, ensure_ascii=False),
            system_prompt=_system_prompt(),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Configured AI provider failed to generate the draft.") from exc

    if not isinstance(response, dict) or response.get("success") is not True:
        raise HTTPException(status_code=502, detail="Configured AI provider did not return a successful generation response.")
    raw = response.get("content")
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(status_code=502, detail="Configured AI provider returned an empty content draft.")
    try:
        draft = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="AI provider returned an invalid structured draft. Retry generation.") from exc
    if not isinstance(draft, dict):
        raise HTTPException(status_code=502, detail="AI provider returned an invalid draft structure.")

    required = ("title", "core_content", "hooks", "caption", "cta", "hashtags")
    if any(key not in draft for key in required):
        raise HTTPException(status_code=502, detail="AI provider draft is missing required content fields.")
    return {
        "success": True,
        "status": "draft",
        "published": False,
        "provider": response.get("provider") or provider_name,
        "model": response.get("model"),
        "draft": draft,
    }
