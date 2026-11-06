"""Authenticated, read-only affiliate product research API."""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_current_user_id
from app.services.digistore24_marketplace import (
    Digistore24MarketplaceClient,
    Digistore24MarketplaceError,
)

router = APIRouter(prefix="/affiliate/research", tags=["affiliate-research"])


@router.get("/digistore24")
async def research_digistore24(
    niche: list[str] = Query(default=["AI tools", "digital marketing", "software"], max_length=8),
    limit: int = Query(default=10, ge=1, le=25),
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Fetch and rank live marketplace records; never publish or create promo links."""
    client = Digistore24MarketplaceClient()
    try:
        return client.research(target_niches=tuple(niche), limit=limit)
    except Digistore24MarketplaceError as exc:
        message = str(exc)
        code = 503 if "not configured" in message.lower() else 502
        raise HTTPException(status_code=code, detail=message) from exc


@router.get("/digistore24/partnership-status")
async def digistore24_partnership_status(
    product_id: str = Query(..., min_length=1, max_length=64),
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Report only locally allowlisted approved products; unknown never means rejected."""
    value = product_id.strip()
    if not value:
        raise HTTPException(status_code=422, detail="product_id must not be blank")
    approved_ids = {
        item.strip()
        for item in os.getenv("DIGISTORE24_APPROVED_PRODUCT_IDS", "").split(",")
        if item.strip()
    }
    approved = value in approved_ids
    return {
        "product_id": value,
        "status": "approved" if approved else "not_confirmed",
        "approval_source": "DIGISTORE24_APPROVED_PRODUCT_IDS" if approved else None,
        "status_is_authoritative": approved,
        "note": (
            "Product is included in the server's configured approved-product allowlist."
            if approved
            else "AEA has no saved confirmation of approval. This does not mean the vendor rejected the partnership; verify it in Digistore24."
        ),
    }


__all__ = ["router"]
