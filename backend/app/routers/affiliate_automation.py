"""Authenticated API for automatically preparing Digistore24 Pinterest Pin drafts."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import get_current_user_id
from app.services.digistore24_marketplace import Digistore24MarketplaceError
from app.services.digistore24_pinterest_draft import build_digistore24_pinterest_draft, build_external_pinterest_draft

router = APIRouter(prefix="/affiliate/automation", tags=["affiliate-automation"])


class PinterestAutopilotRequest(BaseModel):
    niche: str = Field(default="AI tools and digital marketing", min_length=2, max_length=120)
    language: str = Field(default="English", min_length=2, max_length=40)


class ExternalPinterestDraftRequest(PinterestAutopilotRequest):
    affiliate_url: str = Field(min_length=8, max_length=2048)
    product_name: str | None = Field(default=None, max_length=200)


@router.post("/digistore24/pinterest-draft")
def create_pinterest_draft(
    body: PinterestAutopilotRequest,
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Generate product, affiliate destination, Pin copy and image URL from server-side integrations."""
    try:
        return build_digistore24_pinterest_draft(niche=body.niche, language=body.language)
    except Digistore24MarketplaceError as exc:
        message = str(exc)
        code = 503 if "not configured" in message.lower() else 502
        raise HTTPException(status_code=code, detail=message) from exc
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
            niche=body.niche,
            language=body.language,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


__all__ = ["router"]
