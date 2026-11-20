"""Authenticated AEA Creator Community API.

Only creator products explicitly marked community_public are returned.
Database RLS remains the final authority for owner and 18+ eligibility.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict

from app.dependencies import get_current_user_id, get_user_scoped_client

router = APIRouter(prefix="/community", tags=["creator-community"])


class VisibilityRequest(BaseModel):
    visibility: Literal["private", "community_public"]
    is_sensitive: bool = False
    age_rating: Literal["general", "18_plus"] = "general"
    model_config = ConfigDict(extra="forbid")


class AgeVerificationRequest(BaseModel):
    verification_method: Literal["manual_review"] = "manual_review"
    model_config = ConfigDict(extra="forbid")


def _db(client: Any) -> Any:
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is not configured.")
    return client


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    return data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])


@router.get("/feed")
def community_feed(
    limit: int = Query(default=30, ge=1, le=50),
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Return only explicitly community-public creator products allowed by RLS."""
    db = _db(client)
    result = db.table("creator_products").select(
        "id,owner_id,title,description,product_type,visibility,is_sensitive,age_rating,created_at"
    ).eq("visibility", "community_public").order("created_at", desc=True).limit(limit).execute()
    products = _rows(result)
    owner_ids = list({str(row.get("owner_id")) for row in products if row.get("owner_id")})
    profiles: dict[str, dict[str, Any]] = {}
    if owner_ids:
        profile_result = db.table("creator_profiles").select(
            "user_id,display_name,bio,avatar_url,is_public"
        ).in_("user_id", owner_ids).eq("is_public", True).execute()
        profiles = {str(p["user_id"]): p for p in _rows(profile_result) if p.get("user_id")}
    visible = [p for p in products if str(p.get("owner_id")) in profiles]
    return {"success": True, "items": [
        {**p, "creator": profiles[str(p["owner_id"])]} for p in visible
    ]}


@router.patch("/products/{product_id}/visibility")
def update_community_visibility(
    product_id: UUID,
    body: VisibilityRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Let the owner explicitly opt a product into or out of the community."""
    db = _db(client)
    existing = db.table("creator_products").select("id,owner_id").eq(
        "id", str(product_id)
    ).eq("owner_id", owner_id).limit(1).execute()
    if not _rows(existing):
        raise HTTPException(status_code=404, detail="Creator product not found.")
    if body.age_rating == "18_plus" and not body.is_sensitive:
        raise HTTPException(status_code=422, detail="18+ content must carry a sensitive-content label.")
    if body.visibility == "community_public":
        age = db.table("creator_age_verifications").select("status").eq(
            "user_id", owner_id
        ).limit(1).execute()
        age_rows = _rows(age)
        if body.age_rating == "18_plus" and (not age_rows or age_rows[0].get("status") != "verified"):
            raise HTTPException(status_code=403, detail="Verified age status is required before publishing 18+ content to the community.")
    updated = db.table("creator_products").update({
        "visibility": body.visibility,
        "is_sensitive": body.is_sensitive,
        "age_rating": body.age_rating,
    }).eq("id", str(product_id)).eq("owner_id", owner_id).select(
        "id,visibility,is_sensitive,age_rating"
    ).execute()
    rows = _rows(updated)
    if not rows:
        raise HTTPException(status_code=409, detail="Community visibility was not updated.")
    return {"success": True, "product": rows[0], "notice": "Visibility updated. Private products are excluded from the community feed."}


@router.get("/me/age-verification")
def get_age_verification(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    result = db.table("creator_age_verifications").select(
        "status,verified_at,verification_method,updated_at"
    ).eq("user_id", owner_id).limit(1).execute()
    rows = _rows(result)
    return {"success": True, "verification": rows[0] if rows else {"status": "unverified"}}


@router.post("/me/age-verification", status_code=202)
def request_age_verification(
    body: AgeVerificationRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Submit a pending manual-review request; users cannot self-verify."""
    db = _db(client)
    existing = db.table("creator_age_verifications").select("status").eq(
        "user_id", owner_id
    ).limit(1).execute()
    rows = _rows(existing)
    if rows and rows[0].get("status") == "verified":
        return {"success": True, "verification": rows[0], "notice": "Age verification is already confirmed."}
    payload = {"user_id": owner_id, "status": "pending", "verification_method": body.verification_method}
    if rows:
        saved = db.table("creator_age_verifications").update(payload).eq("user_id", owner_id).select(
            "status,verification_method,updated_at"
        ).execute()
    else:
        saved = db.table("creator_age_verifications").insert(payload).select(
            "status,verification_method,created_at"
        ).execute()
    saved_rows = _rows(saved)
    if not saved_rows:
        raise HTTPException(status_code=409, detail="Age verification request could not be saved.")
    return {"success": True, "verification": saved_rows[0], "notice": "Request submitted for manual review. This does not grant 18+ access until verified."}


__all__ = ["router"]
