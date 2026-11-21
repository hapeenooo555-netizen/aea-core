"""Authenticated fal.ai image generation with owner-scoped jobs and private assets."""
from __future__ import annotations

import logging
from typing import Any, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.database import get_supabase_image_budget_client
from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.image_generation_provider import (
    ImageProviderNotConfigured,
    get_image_generation_provider,
)
from app.services.design_text_renderer import DesignCopy, render_design_copy
from app.services.graphic_visual_inspector import inspect_generated_background

router = APIRouter(prefix="/graphic-design", tags=["graphic-design"])
logger = logging.getLogger(__name__)


class DesignTextOverlay(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    body: str = Field(default="", max_length=500)
    cta: str = Field(default="", max_length=80)
    brand: str = Field(default="", max_length=80)
    accent: str = "#10B981"


class ImageGenerateRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=12000)
    aspect_ratio: Literal["1:1", "4:5", "9:16", "16:9", "3:2"] = "1:1"
    model: Literal["z_image_turbo", "nano_banana_pro"] = "z_image_turbo"
    text_overlay: DesignTextOverlay | None = None
    model_config = ConfigDict(extra="forbid")


def _row(response: Any) -> dict[str, Any] | None:
    data = getattr(response, "data", None)
    if isinstance(data, list):
        return data[0] if data else None
    return data if isinstance(data, dict) else None


def _storage_response_data(response: Any) -> dict[str, Any]:
    """Normalize supabase-py storage responses across SDK response shapes."""
    if isinstance(response, dict):
        return response
    data = getattr(response, "data", None)
    return data if isinstance(data, dict) else {}


@router.get("/assets")
def list_graphic_design_assets(
    limit: int = Query(default=30, ge=1, le=50),
    owner_id: str = Depends(get_current_user_id),
    user_client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """List the signed-in owner's completed design assets from private storage."""
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user_client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is unavailable")

    stage = "job_query"
    try:
        response = (
            user_client.table("graphic_design_jobs")
            .select("id,prompt,aspect_ratio,provider,model,status,asset_path,mime_type,width,height,created_at")
            .eq("owner_id", owner_id)
            .eq("status", "completed")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        rows = getattr(response, "data", None) or []
        if not isinstance(rows, list):
            raise RuntimeError("Unexpected graphic-design asset listing response")

        assets = []
        stage = "worker_client_configuration"
        # The bucket is private and its SELECT policy is intentionally not granted
        # to end users. Sign only after owner-filtered DB lookup and path validation.
        signing_client = get_supabase_image_budget_client()
        if signing_client is None:
            raise RuntimeError("Private asset signing is not configured")
        bucket = signing_client.storage.from_("aea-design-assets")
        stage = "asset_signing"
        for row in rows:
            if not isinstance(row, dict):
                continue
            asset_path = row.get("asset_path")
            # Defense in depth: never sign a path outside the authenticated
            # user's folder, even if a privileged writer inserted bad metadata.
            if not isinstance(asset_path, str) or not asset_path.startswith(f"{owner_id}/"):
                continue
            signed = bucket.create_signed_url(asset_path, 3600)
            signed_data = _storage_response_data(signed)
            signed_url = (
                signed_data.get("signedUrl")
                or signed_data.get("signedURL")
                or signed_data.get("signed_url")
            )
            if not signed_url:
                raise RuntimeError("Could not create a temporary asset URL")
            assets.append({
                "job_id": row.get("id"),
                "prompt": row.get("prompt"),
                "aspect_ratio": row.get("aspect_ratio"),
                "provider": row.get("provider"),
                "model": row.get("model"),
                "status": "completed",
                "signed_url": signed_url,
                "signed_url_expires_in": 3600,
                "mime_type": row.get("mime_type"),
                "width": row.get("width"),
                "height": row.get("height"),
                "created_at": row.get("created_at"),
            })
        return {"success": True, "assets": assets, "count": len(assets)}
    except HTTPException:
        raise
    except Exception as exc:
        # Do not log exception text or paths; only stage and exception class.
        logger.error("Graphic design asset listing failed at stage=%s error_type=%s", stage, type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Could not load private graphic-design assets",
        ) from exc


@router.post("/generate", status_code=status.HTTP_201_CREATED)
async def generate_graphic_design_image(
    body: ImageGenerateRequest,
    owner_id: str = Depends(get_current_user_id),
    user_client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Generate one unpublished image and store it in the private owner folder."""
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    if user_client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is unavailable")

    job_id = str(uuid4())
    provider_name = "fal.ai"
    model_id = (
        "fal-ai/z-image/turbo"
        if body.model == "z_image_turbo"
        else "fal-ai/nano-banana-pro"
    )
    try:
        inserted = user_client.table("graphic_design_jobs").insert({
            "id": job_id,
            "owner_id": owner_id,
            "provider": provider_name,
            "model": model_id,
            "prompt": body.prompt,
            "aspect_ratio": body.aspect_ratio,
            "status": "pending",
        }).execute()
        if not _row(inserted):
            raise RuntimeError("Could not create the owner-scoped generation job")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not create image-generation job") from exc

    worker_client = get_supabase_image_budget_client()
    if worker_client is None:
        raise HTTPException(status_code=503, detail="Image storage worker is not configured")

    stage = "provider_configuration"
    try:
        provider = get_image_generation_provider()
        # A text-bearing background is a common image-model failure. Retry only
        # that rejection with a stronger background-only instruction; keep OCR
        # strict so accidental text is never silently accepted.
        inspection = None
        generated = None
        max_generation_attempts = 3
        for attempt in range(1, max_generation_attempts + 1):
            stage = "provider_generation"
            retry_instruction = (
                ""
                if attempt == 1
                else (
                    "\n\nCreate a fresh variation. BACKGROUND ART ONLY: absolutely no "
                    "letters, words, numbers, typography, logos, watermarks, labels, "
                    "signage, signatures, or pseudo-text anywhere in the image. "
                    "Leave all copy areas as clean visual space."
                )
            )
            generated = await provider.generate(
                prompt=body.prompt + retry_instruction,
                aspect_ratio=body.aspect_ratio,
                model=body.model,
            )
            if generated.provider != provider_name:
                raise RuntimeError("Unexpected image provider returned")

            stage = "visual_quality_inspection"
            inspection = inspect_generated_background(generated.content)
            if inspection.accepted:
                if attempt > 1:
                    logger.info(
                        "Graphic design background passed inspection after retry attempt=%s",
                        attempt,
                    )
                break

            # Rejection reason and numeric metrics are safe diagnostics: never log
            # prompts, image bytes, signed URLs, or provider credentials.
            logger.warning(
                "Graphic design visual inspection rejected image attempt=%s reason=%s metrics=%s",
                attempt,
                inspection.reason,
                inspection.quality_metrics or {},
            )
            if inspection.reason != "unwanted_text_detected":
                raise ValueError(
                    f"Visual inspection rejected image: {inspection.reason}"
                )

        if generated is None or inspection is None or not inspection.accepted:
            raise ValueError(
                "Generated backgrounds repeatedly contained unwanted text"
            )
        final_content = generated.content
        final_mime_type = generated.mime_type
        final_width = generated.width
        final_height = generated.height
        if body.text_overlay is not None:
            stage = "text_overlay_render"
            final_content, final_width, final_height = render_design_copy(
                generated.content,
                DesignCopy(**body.text_overlay.model_dump()),
            )
            final_mime_type = "image/png"
        extension = {
            "image/png": "png",
            "image/jpeg": "jpg",
            "image/webp": "webp",
        }.get(final_mime_type)
        if extension is None:
            raise RuntimeError("Unsupported generated image type")
        asset_path = f"{owner_id}/{job_id}/original.{extension}"
        stage = "private_storage_upload"
        worker_client.storage.from_("aea-design-assets").upload(
            asset_path,
            final_content,
            file_options={"content-type": final_mime_type, "upsert": "false"},
        )
        stage = "job_finalization"
        updated = worker_client.table("graphic_design_jobs").update({
            "status": "completed",
            "asset_path": asset_path,
            "mime_type": final_mime_type,
            "width": final_width,
            "height": final_height,
            "provider_metadata": {**generated.provider_metadata, "visual_inspection": {"accepted": True, "reason": inspection.reason, "quality_metrics": inspection.quality_metrics}},
        }).eq("id", job_id).eq("owner_id", owner_id).execute()
        if not _row(updated):
            raise RuntimeError("Could not finalize image-generation job")

        stage = "signed_url_creation"
        # Upload and signing use the server-side worker client. The path is
        # constructed from the authenticated owner and this newly-created job ID.
        signed = worker_client.storage.from_("aea-design-assets").create_signed_url(
            asset_path, 3600
        )
        signed_data = _storage_response_data(signed)
        signed_url = (
            signed_data.get("signedUrl")
            or signed_data.get("signedURL")
            or signed_data.get("signed_url")
        )
        return {
            "success": True,
            "job_id": job_id,
            "status": "completed",
            "published": False,
            "provider": generated.provider,
            "model": generated.model,
            "asset_path": asset_path,
            "signed_url": signed_url,
            "signed_url_expires_in": 3600 if signed_url else None,
            "mime_type": final_mime_type,
            "width": final_width,
            "height": final_height,
        }
    except ImageProviderNotConfigured as exc:
        logger.warning("Graphic design generation unavailable at stage=%s error_type=%s", stage, type(exc).__name__)
        worker_client.table("graphic_design_jobs").update({
            "status": "failed", "error": str(exc)[:500]
        }).eq("id", job_id).eq("owner_id", owner_id).execute()
        raise HTTPException(status_code=503, detail="Image generation is not configured") from exc
    except Exception as exc:
        # Never log exception text: provider errors may contain prompts, signed URLs,
        # request metadata, or credentials. Stage and exception class are sufficient.
        logger.error("Graphic design generation failed at stage=%s error_type=%s", stage, type(exc).__name__)
        try:
            worker_client.table("graphic_design_jobs").update({
                "status": "failed", "error": "Image generation or private asset storage failed"
            }).eq("id", job_id).eq("owner_id", owner_id).execute()
        except Exception:
            pass
        raise HTTPException(
            status_code=502,
            detail="Image generation failed; the image was not published.",
        ) from exc
