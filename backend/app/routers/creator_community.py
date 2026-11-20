"""Authenticated AEA Creator Community API.

Only creator products explicitly marked community_public are returned.
Database RLS remains the final authority for owner and 18+ eligibility.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field

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



class ReactionRequest(BaseModel):
    reaction: Literal["like", "love", "insightful", "inspiring"]
    model_config = ConfigDict(extra="forbid")


class CommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=3000)
    model_config = ConfigDict(extra="forbid")


@router.get("/products/{product_id}/engagement")
def product_engagement(product_id: UUID, limit: int = Query(default=30, ge=1, le=100),
    owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)):
    db = _db(client)
    reactions = _rows(db.table("creator_product_reactions").select("user_id,reaction,created_at")
        .eq("product_id", str(product_id)).order("created_at", desc=True).limit(limit).execute())
    comments = _rows(db.table("creator_product_comments").select("id,user_id,body,created_at")
        .eq("product_id", str(product_id)).order("created_at", desc=True).limit(limit).execute())
    return {"success": True, "reactions": reactions, "comments": comments}


@router.put("/products/{product_id}/reaction")
def set_product_reaction(product_id: UUID, body: ReactionRequest,
    owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)):
    db = _db(client)
    try:
        result = db.table("creator_product_reactions").upsert(
            {"product_id": str(product_id), "user_id": owner_id, "reaction": body.reaction},
            on_conflict="product_id,user_id").execute()
    except Exception as exc:
        raise HTTPException(status_code=403, detail="Reaction is not allowed for this product or account.") from exc
    rows = _rows(result)
    if not rows:
        raise HTTPException(status_code=403, detail="Reaction was not accepted by database policy.")
    return {"success": True, "reaction": rows[0]}


@router.delete("/products/{product_id}/reaction")
def remove_product_reaction(product_id: UUID, owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client)):
    _db(client).table("creator_product_reactions").delete().eq(
        "product_id", str(product_id)).eq("user_id", owner_id).execute()
    return {"success": True}


@router.post("/products/{product_id}/comments", status_code=201)
def add_product_comment(product_id: UUID, body: CommentRequest,
    owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)):
    db = _db(client)
    try:
        result = db.table("creator_product_comments").insert(
            {"product_id": str(product_id), "user_id": owner_id, "body": body.body.strip()}).execute()
    except Exception as exc:
        raise HTTPException(status_code=403, detail="Comment is not allowed for this product or account.") from exc
    rows = _rows(result)
    if not rows:
        raise HTTPException(status_code=403, detail="Comment was not accepted by database policy.")
    return {"success": True, "comment": rows[0]}


@router.delete("/products/{product_id}/comments/{comment_id}")
def delete_product_comment(product_id: UUID, comment_id: UUID,
    owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)):
    _db(client).table("creator_product_comments").delete().eq(
        "id", str(comment_id)).eq("product_id", str(product_id)).eq("user_id", owner_id).execute()
    return {"success": True}


@router.post("/creators/{creator_id}/follow", status_code=201)
def follow_creator(creator_id: UUID, owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client)):
    if str(creator_id) == owner_id:
        raise HTTPException(status_code=422, detail="You cannot follow your own creator profile.")
    db = _db(client)
    try:
        result = db.table("creator_follows").upsert(
            {"follower_id": owner_id, "creator_id": str(creator_id)},
            on_conflict="follower_id,creator_id").execute()
    except Exception as exc:
        raise HTTPException(status_code=403, detail="Follow request was not accepted.") from exc
    rows = _rows(result)
    return {"success": True, "follow": rows[0] if rows else {"creator_id": str(creator_id)}}


@router.delete("/creators/{creator_id}/follow")
def unfollow_creator(creator_id: UUID, owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client)):
    _db(client).table("creator_follows").delete().eq(
        "follower_id", owner_id).eq("creator_id", str(creator_id)).execute()
    return {"success": True}


__all__ = ["router"]
