"""Authenticated Creator Community API backed by owner-scoped Supabase RLS."""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client

router = APIRouter(prefix="/community", tags=["creator-community"])


class CreatorProfileUpdate(BaseModel):
    display_name: str = Field(min_length=2, max_length=100)
    bio: str = Field(default="", max_length=1000)
    avatar_url: str | None = Field(default=None, max_length=2000)
    is_public: bool = False
    model_config = ConfigDict(extra="forbid")


class AgeVerificationRequest(BaseModel):
    verification_method: Literal["manual_review"] = "manual_review"
    model_config = ConfigDict(extra="forbid")


class ReactionRequest(BaseModel):
    reaction: Literal["like"] = "like"
    model_config = ConfigDict(extra="forbid")


class CommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=3000)
    model_config = ConfigDict(extra="forbid")


def _db(client: Any) -> Any:
    if client is None:
        raise HTTPException(status_code=503, detail="Creator Community database access is unavailable.")
    return client


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    return data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])


def _db_unavailable() -> HTTPException:
    return HTTPException(status_code=503, detail="Creator Community data service is unavailable.")


@router.get("/feed")
def community_feed(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Return only community-public products from public creator profiles.

    Product RLS additionally enforces the viewer's age-verification eligibility.
    """
    db = _db(client)
    try:
        products = _rows(
            db.table("creator_products")
            .select("id,owner_id,title,description,product_type,is_sensitive,age_rating,created_at")
            .eq("visibility", "community_public")
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        if not products:
            return {"success": True, "items": []}
        creator_ids = list({str(row["owner_id"]) for row in products if row.get("owner_id")})
        profiles = _rows(
            db.table("creator_profiles")
            .select("user_id,display_name,bio,avatar_url")
            .in_("user_id", creator_ids)
            .eq("is_public", True)
            .execute()
        )
        profile_by_id = {str(p["user_id"]): p for p in profiles}
        items = []
        for product in products:
            creator = profile_by_id.get(str(product.get("owner_id")))
            if not creator:
                continue
            items.append({
                **product,
                "creator": {
                    "user_id": str(creator["user_id"]),
                    "display_name": creator.get("display_name"),
                    "bio": creator.get("bio") or "",
                    "avatar_url": creator.get("avatar_url"),
                },
            })
        return {"success": True, "items": items}
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_unavailable() from exc


@router.get("/me/profile")
def get_my_creator_profile(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    try:
        rows = _rows(db.table("creator_profiles").select(
            "user_id,display_name,bio,avatar_url,is_public"
        ).eq("user_id", owner_id).limit(1).execute())
        profile = rows[0] if rows else None
        if profile:
            profile = {
                "display_name": profile.get("display_name") or "",
                "bio": profile.get("bio") or "",
                "avatar_url": profile.get("avatar_url"),
                "is_public": bool(profile.get("is_public")),
            }
        return {"success": True, "profile": profile}
    except Exception as exc:
        raise _db_unavailable() from exc


@router.put("/me/profile")
def save_my_creator_profile(
    body: CreatorProfileUpdate,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    payload = body.model_dump()
    payload["user_id"] = owner_id
    try:
        rows = _rows(db.table("creator_profiles").upsert(
            payload, on_conflict="user_id"
        ).execute())
        if not rows:
            raise HTTPException(status_code=502, detail="Database did not confirm the creator profile update.")
        profile = rows[0]
        return {"success": True, "profile": {
            "display_name": profile.get("display_name") or "",
            "bio": profile.get("bio") or "",
            "avatar_url": profile.get("avatar_url"),
            "is_public": bool(profile.get("is_public")),
        }}
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_unavailable() from exc


@router.get("/me/age-verification")
def get_my_age_verification(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    try:
        rows = _rows(db.table("creator_age_verifications").select(
            "status,verified_at,verification_method"
        ).eq("user_id", owner_id).limit(1).execute())
        return {"success": True, "verification": rows[0] if rows else {"status": "unverified"}}
    except Exception as exc:
        raise _db_unavailable() from exc


@router.post("/me/age-verification")
def request_age_verification(
    body: AgeVerificationRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Submit a pending manual review; users cannot self-verify."""
    db = _db(client)
    try:
        existing = _rows(db.table("creator_age_verifications").select(
            "status,verified_at,verification_method"
        ).eq("user_id", owner_id).limit(1).execute())
        if existing and existing[0].get("status") == "verified":
            return {"success": True, "verification": existing[0], "notice": "Umri wako tayari umethibitishwa."}
        if existing and existing[0].get("status") == "pending":
            return {"success": True, "verification": existing[0], "notice": "Ombi lako linasubiri ukaguzi wa manual."}
        rows = _rows(db.table("creator_age_verifications").upsert({
            "user_id": owner_id,
            "status": "pending",
            "verification_method": body.verification_method,
            "verified_at": None,
        }, on_conflict="user_id").execute())
        if not rows:
            raise HTTPException(status_code=502, detail="Database did not confirm the age-verification request.")
        return {"success": True, "verification": rows[0], "notice": "Ombi limetumwa kwa ukaguzi wa manual."}
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_unavailable() from exc


@router.get("/products/{product_id}/engagement")
def get_product_engagement(
    product_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    try:
        reactions = _rows(db.table("creator_product_reactions").select(
            "user_id,reaction,created_at"
        ).eq("product_id", str(product_id)).order("created_at").limit(500).execute())
        comments = _rows(db.table("creator_product_comments").select(
            "id,user_id,body,created_at"
        ).eq("product_id", str(product_id)).order("created_at").limit(100).execute())
        return {"success": True, "reactions": reactions, "comments": comments}
    except Exception as exc:
        raise _db_unavailable() from exc


@router.put("/products/{product_id}/reaction")
def set_product_reaction(
    product_id: UUID,
    body: ReactionRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    try:
        existing = _rows(db.table("creator_product_reactions").select("id").eq(
            "product_id", str(product_id)
        ).eq("user_id", owner_id).limit(1).execute())
        if existing:
            saved = db.table("creator_product_reactions").update({
                "reaction": body.reaction
            }).eq("id", existing[0]["id"]).execute()
        else:
            saved = db.table("creator_product_reactions").insert({
                "product_id": str(product_id),
                "user_id": owner_id,
                "reaction": body.reaction,
            }).execute()
        if not _rows(saved):
            raise HTTPException(status_code=502, detail="Database did not confirm the reaction.")
        return {"success": True, "reaction": body.reaction}
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_unavailable() from exc


@router.post("/products/{product_id}/comments", status_code=201)
def create_product_comment(
    product_id: UUID,
    body: CommentRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    try:
        rows = _rows(db.table("creator_product_comments").insert({
            "product_id": str(product_id),
            "user_id": owner_id,
            "body": body.body.strip(),
        }).execute())
        if not rows:
            raise HTTPException(status_code=502, detail="Database did not confirm the comment.")
        return {"success": True, "comment": rows[0]}
    except HTTPException:
        raise
    except Exception as exc:
        raise _db_unavailable() from exc


@router.post("/creators/{creator_id}/follow")
def follow_creator(
    creator_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    if str(creator_id) == owner_id:
        raise HTTPException(status_code=400, detail="Huwezi kujifuatilia mwenyewe.")
    db = _db(client)
    try:
        existing = _rows(db.table("creator_follows").select("creator_id").eq(
            "follower_id", owner_id
        ).eq("creator_id", str(creator_id)).limit(1).execute())
        if not existing:
            db.table("creator_follows").insert({
                "follower_id": owner_id,
                "creator_id": str(creator_id),
            }).execute()
        return {"success": True, "following": True}
    except Exception as exc:
        raise _db_unavailable() from exc
