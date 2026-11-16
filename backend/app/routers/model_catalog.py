"""Authenticated read-only endpoints for the extensible AI model catalogue."""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_current_user_id
from app.services.model_router import get_model_catalog, load_catalog, route_model

router = APIRouter(prefix="/models", tags=["model-routing"])


@router.get("/catalog")
def model_catalog(owner_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    return {"success": True, "models": get_model_catalog()}


@router.get("/recommend")
def recommend_model(
    capability: str = Query(min_length=2, max_length=80),
    quality: str = Query(default="balanced", pattern="^(economy|balanced|premium)$"),
    max_cost_per_unit: float | None = Query(default=None, ge=0),
    preferred_provider: str | None = Query(default=None, min_length=1, max_length=80),
    owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        model = route_model(load_catalog(), capability, quality, max_cost_per_unit, preferred_provider)
    except LookupError:
        return {"success": True, "model": None, "reason": "No enabled model currently supports this capability and budget."}
    return {"success": True, "model": model.public_dict()}
