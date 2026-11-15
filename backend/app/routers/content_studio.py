"""Authenticated AI Content Studio generation and durable asset endpoints.

Generated content is always an unpublished draft. Asset revisions are append-only:
each save creates a new immutable version, preserving version history.
"""
from __future__ import annotations

import json
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client
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


class StudioAssetCreateRequest(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=128)
    title: str = Field(min_length=1, max_length=300)
    content_type: ContentType
    niche: str = Field(min_length=1, max_length=160)
    prompt: str = Field(min_length=1, max_length=12000)
    content: dict[str, Any]
    model_config = ConfigDict(extra="forbid")


class StudioAssetRevisionRequest(StudioAssetCreateRequest):
    expected_version: int = Field(ge=1)


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


def _require_user_client(client: Any) -> Any:
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="User-scoped database access is not configured.",
        )
    return client


def _response_row(response: Any) -> dict[str, Any] | None:
    data = getattr(response, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


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


@router.post("/assets", status_code=status.HTTP_201_CREATED)
def create_studio_asset(
    body: StudioAssetCreateRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Persist a new owner-scoped draft as immutable version 1."""
    db = _require_user_client(client)
    existing = db.table("content_asset_versions").select("*").eq(
        "owner_id", owner_id
    ).eq("idempotency_key", body.idempotency_key).limit(1).execute()
    existing_row = _response_row(existing)
    if existing_row:
        return {"success": True, "asset": existing_row, "replayed": True}

    asset_id = str(uuid4())
    payload = {
        "asset_id": asset_id,
        "owner_id": owner_id,
        "version": 1,
        "idempotency_key": body.idempotency_key,
        "title": body.title,
        "content_type": body.content_type,
        "niche": body.niche,
        "prompt": body.prompt,
        "content": body.content,
        "status": "draft",
    }
    try:
        saved = db.table("content_asset_versions").insert(payload).execute()
    except Exception as exc:
        # A concurrent retry may win the unique idempotency constraint.
        replay = db.table("content_asset_versions").select("*").eq(
            "owner_id", owner_id
        ).eq("idempotency_key", body.idempotency_key).limit(1).execute()
        replay_row = _response_row(replay)
        if replay_row:
            return {"success": True, "asset": replay_row, "replayed": True}
        raise HTTPException(status_code=409, detail="Draft could not be saved because of a concurrent version conflict.") from exc
    row = _response_row(saved)
    if not row:
        raise HTTPException(status_code=502, detail="Database did not confirm draft persistence.")
    return {"success": True, "asset": row, "replayed": False}


@router.get("/assets")
def list_studio_assets(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Return the latest saved version of each draft owned by the caller."""
    db = _require_user_client(client)
    response = db.table("content_assets_latest").select("*").eq(
        "owner_id", owner_id
    ).order("updated_at", desc=True).limit(100).execute()
    return {"success": True, "assets": getattr(response, "data", None) or []}


@router.get("/assets/{asset_id}/versions")
def list_studio_asset_versions(
    asset_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Return immutable version history for one asset, newest first."""
    db = _require_user_client(client)
    response = db.table("content_asset_versions").select("*").eq(
        "owner_id", owner_id
    ).eq("asset_id", str(asset_id)).order("version", desc=True).limit(100).execute()
    return {"success": True, "versions": getattr(response, "data", None) or []}


@router.patch("/assets/{asset_id}")
def revise_studio_asset(
    asset_id: UUID,
    body: StudioAssetRevisionRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Save an edit as a new immutable version; never overwrite prior content."""
    db = _require_user_client(client)
    replay = db.table("content_asset_versions").select("*").eq(
        "owner_id", owner_id
    ).eq("idempotency_key", body.idempotency_key).limit(1).execute()
    replay_row = _response_row(replay)
    if replay_row:
        return {"success": True, "asset": replay_row, "replayed": True}

    latest_response = db.table("content_asset_versions").select("*").eq(
        "owner_id", owner_id
    ).eq("asset_id", str(asset_id)).order("version", desc=True).limit(1).execute()
    latest = _response_row(latest_response)
    if not latest:
        raise HTTPException(status_code=404, detail="Content asset not found.")
    if latest.get("version") != body.expected_version:
        raise HTTPException(status_code=409, detail="This draft has changed. Reload the latest version before saving.")

    payload = {
        "asset_id": str(asset_id),
        "owner_id": owner_id,
        "version": int(latest["version"]) + 1,
        "idempotency_key": body.idempotency_key,
        "title": body.title,
        "content_type": body.content_type,
        "niche": body.niche,
        "prompt": body.prompt,
        "content": body.content,
        "status": "draft",
    }
    try:
        saved = db.table("content_asset_versions").insert(payload).execute()
    except Exception as exc:
        raise HTTPException(status_code=409, detail="This draft was updated by another request. Reload its latest version.") from exc
    row = _response_row(saved)
    if not row:
        raise HTTPException(status_code=502, detail="Database did not confirm the new draft version.")
    return {"success": True, "asset": row, "replayed": False}
