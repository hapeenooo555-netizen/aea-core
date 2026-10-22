"""Authenticated, read-only affiliate product research API."""
from __future__ import annotations

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


__all__ = ["router"]
