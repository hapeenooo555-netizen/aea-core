"""Authenticated API for preparing Pinterest drafts from user and marketplace inputs."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import get_current_user_id
from app.services.pinterest_draft import build_external_pinterest_draft, build_external_pinterest_drafts, build_marketplace_pinterest_draft

router = APIRouter(prefix="/affiliate/automation", tags=["affiliate-automation"])


class PinterestAutopilotRequest(BaseModel):
    niche: str = Field(default="Marketplace", min_length=2, max_length=120)
    language: str = Field(default="English", min_length=2, max_length=40)


class ExternalPinterestDraftRequest(PinterestAutopilotRequest):
    affiliate_url: str = Field(min_length=8, max_length=2048)
    product_name: str | None = Field(default=None, max_length=200)
    product_description: str | None = Field(default=None, max_length=3000)



class MarketplacePinterestDraftRequest(BaseModel):
    affiliate_url: str = Field(min_length=8, max_length=2048)
    product_url: str = Field(min_length=8, max_length=2048)
    product_name: str = Field(min_length=1, max_length=200)
    product_description: str = Field(default="", max_length=3000)
    product_image_url: str = Field(min_length=8, max_length=2048)
    provider: str = Field(min_length=2, max_length=40)
    niche: str = Field(default="Marketplace", min_length=2, max_length=120)
    language: str = Field(default="English", min_length=2, max_length=40)


@router.post("/marketplace/pinterest-draft")
def create_marketplace_pinterest_draft(
    body: MarketplacePinterestDraftRequest,
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Create a Pin draft from marketplace product data and a user-confirmed affiliate URL."""
    try:
        return build_marketplace_pinterest_draft(
            affiliate_url=body.affiliate_url,
            product_url=body.product_url,
            product_name=body.product_name,
            product_description=body.product_description,
            product_image_url=body.product_image_url,
            provider=body.provider,
            niche=body.niche,
            language=body.language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/pinterest-draft")
def create_external_pinterest_draft(
    body: ExternalPinterestDraftRequest,
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Prepare a Pin from a user-supplied affiliate URL; does not publish."""
    try:
        return build_external_pinterest_draft(
            affiliate_url=body.affiliate_url,
            product_name=body.product_name,
            product_description=body.product_description,
            niche=body.niche,
            language=body.language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/pinterest-drafts")
def create_external_pinterest_drafts(
    body: ExternalPinterestDraftRequest,
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Generate three editable Pin copy options using AEA Core's configured LLM."""
    try:
        return build_external_pinterest_drafts(
            affiliate_url=body.affiliate_url,
            product_name=body.product_name,
            product_description=body.product_description,
            niche=body.niche,
            language=body.language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["router"]
