"""Authenticated AEA creator store, marketplace, promotion and distribution APIs.

All database access uses the caller's JWT and relies on Supabase RLS.
No checkout, payment, delivery, commission settlement or payout is performed.
"""
from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client

router = APIRouter(prefix="/store/products", tags=["creator-store-marketplace"])


class DistributionRequest(BaseModel):
    destination: Literal["pinterest", "facebook", "instagram", "tiktok", "youtube"]
    model_config = ConfigDict(extra="forbid")


class MarketplaceToggleRequest(BaseModel):
    enabled: bool
    model_config = ConfigDict(extra="forbid")


class VisibilityRequest(BaseModel):
    visibility: Literal["private", "community_public"]
    is_sensitive: bool = False
    age_rating: Literal["general", "18_plus"] = "general"
    model_config = ConfigDict(extra="forbid")


class ListingReviewRequest(BaseModel):
    status: Literal["approved", "rejected"]
    model_config = ConfigDict(extra="forbid")


class PromotionCreateRequest(BaseModel):
    listing_id: UUID
    message: str = Field(default="", max_length=2000)
    model_config = ConfigDict(extra="forbid")


class PromotionReviewRequest(BaseModel):
    status: Literal["approved", "rejected"]
    commission_rate_bps: int | None = Field(default=None, ge=1, le=10000)
    model_config = ConfigDict(extra="forbid")


def _db(client: Any) -> Any:
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is unavailable.")
    return client


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    return data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])


def _one(db: Any, table: str, columns: str, **filters: str) -> dict[str, Any] | None:
    query = db.table(table).select(columns)
    for key, value in filters.items():
        query = query.eq(key, value)
    rows = _rows(query.limit(1).execute())
    return rows[0] if rows else None


def _is_admin(db: Any, user_id: str) -> bool:
    row = _one(db, "aea_marketplace_admins", "user_id,active", user_id=user_id)
    return bool(row and row.get("active") is True)


def _require_admin(db: Any, user_id: str) -> None:
    if not _is_admin(db, user_id):
        raise HTTPException(status_code=403, detail="Marketplace Admin permission required.")


DISTRIBUTION_CAPABILITIES = {
    "pinterest": {"publishing_supported": False, "approval_required": True, "connection_required": True, "mode": "draft_only", "message": "Pinterest connector exists, but end-to-end OAuth, approval, publish and provider verification are not yet confirmed. You can save a draft."},
    "facebook": {"publishing_supported": False, "approval_required": True, "connection_required": True, "mode": "draft_only", "message": "Publishing connector is not yet enabled. You can save a distribution draft."},
    "instagram": {"publishing_supported": False, "approval_required": True, "connection_required": True, "mode": "draft_only", "message": "Publishing connector is not yet enabled. You can save a distribution draft."},
    "tiktok": {"publishing_supported": False, "approval_required": True, "connection_required": True, "mode": "draft_only", "message": "Publishing connector is not yet enabled. You can save a distribution draft."},
    "youtube": {"publishing_supported": False, "approval_required": True, "connection_required": True, "mode": "draft_only", "message": "Publishing connector is not yet enabled. You can save a distribution draft."},
}

@router.get("/distribution/capabilities")
def get_distribution_capabilities(
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Expose truthful per-platform publishing support to My Store."""
    return {
        "success": True,
        "capabilities": [
            {"destination": destination, **details}
            for destination, details in DISTRIBUTION_CAPABILITIES.items()
        ],
    }

@router.get("/mine/distribution")
def list_my_distribution_jobs(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    # Keep this query compatible with the pre-existing Hub table schema.
    # New lifecycle columns are added by a separate ordered DB migration;
    # history must remain readable before that migration is applied.
    jobs = _rows(db.table("creator_distribution_jobs").select(
        "id,product_id,destination,status,error,created_at,scheduled_at,"
        "approval_required,approved_at,external_id,external_url"
    ).eq("owner_id", owner_id).order("created_at", desc=True).limit(200).execute())
    published_pins = _rows(db.table("published_pins").select(
        "id,approval_request_id,board_name,pin_id,status,created_at,content"
    ).eq("owner_id", owner_id).eq("platform", "pinterest").eq("status", "published")
     .order("created_at", desc=True).limit(200).execute())
    verified_pins = []
    for pin in published_pins:
        pin_id = str(pin.get("pin_id") or "").strip()
        if not pin_id:
            continue
        verified_pins.append({
            "id": pin.get("id"),
            "approval_request_id": pin.get("approval_request_id"),
            "board_name": pin.get("board_name"),
            "pin_id": pin_id,
            "pin_url": f"https://www.pinterest.com/pin/{pin_id}/",
            "published_at": (pin.get("content") or {}).get("published_at") or pin.get("created_at"),
            "status": "published",
        })
    return {"success": True, "jobs": jobs, "published_pins": verified_pins}


@router.post("/{product_id}/distribution", status_code=201)
def create_distribution_job(
    product_id: UUID,
    body: DistributionRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    product = _one(db, "creator_products", "id,owner_id,status,completion_status", id=str(product_id), owner_id=owner_id)
    if not product:
        raise HTTPException(status_code=404, detail="Creator product not found.")
    if product.get("status") != "listed" or product.get("completion_status") != "finished":
        raise HTTPException(status_code=409, detail="Finish and list the product before creating distribution drafts.")
    # Repeated taps/retries must not create duplicate draft requests for the
    # same owner, product and destination. This is draft deduplication only;
    # it does not claim that approval or external publishing has occurred.
    existing_draft = _one(
        db,
        "creator_distribution_jobs",
        "id,product_id,owner_id,destination,status,approval_required,created_at",
        product_id=str(product_id),
        owner_id=owner_id,
        destination=body.destination,
        status="draft",
    )
    if existing_draft:
        return {
            "success": True,
            "job": existing_draft,
            "published": False,
            "duplicate": True,
        }
    try:
        rows = _rows(db.table("creator_distribution_jobs").insert({
            "product_id": str(product_id),
            "owner_id": owner_id,
            "destination": body.destination,
            "status": "draft",
            "approval_required": True,
        }).select("*").execute())
    except Exception as exc:
        # A concurrent request may have won the partial unique-index race.
        # Re-read the durable draft and return it rather than creating a
        # second logical request or surfacing a misleading server error.
        concurrent_draft = _one(
            db,
            "creator_distribution_jobs",
            "id,product_id,owner_id,destination,status,approval_required,created_at",
            product_id=str(product_id),
            owner_id=owner_id,
            destination=body.destination,
            status="draft",
        )
        if concurrent_draft:
            return {
                "success": True,
                "job": concurrent_draft,
                "published": False,
                "duplicate": True,
            }
        raise HTTPException(status_code=409, detail="Distribution request could not be saved.") from exc
    if not rows:
        raise HTTPException(status_code=502, detail="Database did not confirm distribution request.")
    return {"success": True, "job": rows[0], "published": False}


@router.patch("/{product_id}/marketplace")
def set_marketplace_enabled(
    product_id: UUID,
    body: MarketplaceToggleRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    product = _one(db, "creator_products", "id,status,completion_status,marketplace_enabled", id=str(product_id), owner_id=owner_id)
    if not product:
        raise HTTPException(status_code=404, detail="Creator product not found.")
    if body.enabled and (product.get("status") != "listed" or product.get("completion_status") != "finished"):
        raise HTTPException(status_code=409, detail="Finish and list the product before enabling Marketplace discovery.")
    rows = _rows(db.table("creator_products").update({
        "marketplace_enabled": body.enabled
    }).eq("id", str(product_id)).eq("owner_id", owner_id).select("*").execute())
    if not rows:
        raise HTTPException(status_code=409, detail="Marketplace setting was not saved.")
    return {"success": True, "product": rows[0]}


@router.patch("/{product_id}/visibility")
def set_community_visibility(
    product_id: UUID,
    body: VisibilityRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    if body.visibility == "community_public":
        profile = _one(db, "creator_profiles", "user_id,is_public", user_id=owner_id)
        if not profile or not profile.get("is_public"):
            raise HTTPException(status_code=409, detail="Make your creator profile public before publishing work to Community.")
    # Keep the sensitivity flag and age gate aligned. This prevents a client from
    # marking a sensitive/18+ item as general-audience through inconsistent fields.
    is_sensitive = body.is_sensitive or body.age_rating == "18_plus"
    age_rating = "18_plus" if is_sensitive else "general"
    rows = _rows(db.table("creator_products").update({
        "visibility": body.visibility,
        "is_sensitive": is_sensitive,
        "age_rating": age_rating,
    }).eq("id", str(product_id)).eq("owner_id", owner_id).select("*").execute())
    if not rows:
        raise HTTPException(status_code=404, detail="Creator product not found.")
    return {"success": True, "product": rows[0]}


@router.post("/{product_id}/marketplace/listing", status_code=201)
def submit_marketplace_listing(
    product_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    product = _one(db, "creator_products", "id,owner_id,status,completion_status,marketplace_enabled,price_amount,currency", id=str(product_id), owner_id=owner_id)
    if not product:
        raise HTTPException(status_code=404, detail="Creator product not found.")
    if product.get("status") != "listed" or product.get("completion_status") != "finished" or not product.get("marketplace_enabled"):
        raise HTTPException(status_code=409, detail="Finish the product, list it and enable Marketplace discovery first.")
    existing = _one(db, "creator_marketplace_listings", "id,status", product_id=str(product_id), owner_id=owner_id)
    if existing and existing.get("status") == "pending":
        return {"success": True, "listing": existing, "notice": "Listing is already awaiting review."}
    if existing and existing.get("status") == "approved":
        return {"success": True, "listing": existing, "notice": "Listing is already approved."}
    try:
        rows = _rows(db.table("creator_marketplace_listings").insert({
            "product_id": str(product_id),
            "owner_id": owner_id,
            "status": "pending",
            "price_amount": product.get("price_amount"),
            "currency": product.get("currency") or "TZS",
        }).select("*").execute())
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Marketplace listing request could not be saved.") from exc
    if not rows:
        raise HTTPException(status_code=502, detail="Database did not confirm listing request.")
    return {"success": True, "listing": rows[0]}


@router.get("/marketplace")
def browse_marketplace(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    listings = _rows(db.table("creator_marketplace_listings").select(
        "id,product_id,owner_id,status,price_amount,currency,created_at"
    ).eq("status", "approved").order("created_at", desc=True).limit(100).execute())
    if not listings:
        return {"success": True, "listings": []}
    product_ids = list({row["product_id"] for row in listings})
    products = _rows(db.table("creator_products").select(
        "id,slug,title,description,product_type,price_amount,currency,age_rating,owner_id"
    ).in_("id", product_ids).execute())
    owner_ids = list({row["owner_id"] for row in listings})
    profiles = _rows(db.table("creator_profiles").select(
        "user_id,display_name"
    ).in_("user_id", owner_ids).eq("is_public", True).execute())
    product_map = {str(row["id"]): row for row in products}
    profile_map = {str(row["user_id"]): row for row in profiles}
    items = []
    for listing in listings:
        product = product_map.get(str(listing["product_id"]))
        vendor = profile_map.get(str(listing["owner_id"]))
        if product and vendor:
            items.append({"listing": listing, "product": product, "vendor": vendor})
    return {"success": True, "listings": items}


@router.get("/marketplace/requests/mine")
def list_my_promotion_requests(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    rows = _rows(db.table("creator_promotion_requests").select(
        "id,listing_id,product_id,owner_id,promoter_id,status,message,created_at"
    ).or_(f"owner_id.eq.{owner_id},promoter_id.eq.{owner_id}").order("created_at", desc=True).limit(200).execute())
    return {"success": True, "requests": rows}


@router.post("/marketplace/requests", status_code=201)
def create_promotion_request(
    body: PromotionCreateRequest,
    promoter_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    listing = _one(db, "creator_marketplace_listings", "id,product_id,owner_id,status", id=str(body.listing_id))
    if not listing or listing.get("status") != "approved":
        raise HTTPException(status_code=404, detail="Approved marketplace listing not found.")
    if listing.get("owner_id") == promoter_id:
        raise HTTPException(status_code=400, detail="You cannot request promotion for your own product.")
    existing = _rows(db.table("creator_promotion_requests").select(
        "id,status"
    ).eq("listing_id", str(body.listing_id)).eq("promoter_id", promoter_id).limit(1).execute())
    if existing and existing[0].get("status") in {"pending", "approved"}:
        return {"success": True, "request": existing[0]}
    try:
        rows = _rows(db.table("creator_promotion_requests").insert({
            "listing_id": str(body.listing_id),
            "product_id": listing["product_id"],
            "owner_id": listing["owner_id"],
            "promoter_id": promoter_id,
            "status": "pending",
            "message": body.message.strip(),
        }).select("*").execute())
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Promotion request could not be saved.") from exc
    if not rows:
        raise HTTPException(status_code=502, detail="Database did not confirm promotion request.")
    return {"success": True, "request": rows[0]}


@router.patch("/marketplace/requests/{request_id}")
def review_promotion_request(
    request_id: UUID,
    body: PromotionReviewRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    request = _one(db, "creator_promotion_requests", "id,owner_id,status", id=str(request_id), owner_id=owner_id)
    if not request:
        raise HTTPException(status_code=404, detail="Promotion request not found.")
    if request.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Only pending promotion requests can be reviewed.")
    if body.status == "approved" and body.commission_rate_bps is None:
        raise HTTPException(status_code=422, detail="Set the affiliate commission rate (1–10000 basis points) before approving promotion.")
    update_payload = {"status": body.status}
    if body.status == "approved":
        update_payload["commission_rate_bps"] = body.commission_rate_bps
    rows = _rows(db.table("creator_promotion_requests").update(
        update_payload
    ).eq("id", str(request_id)).eq("owner_id", owner_id).eq("status", "pending").select("*").execute())
    if not rows:
        raise HTTPException(status_code=409, detail="Promotion decision was not saved.")
    return {"success": True, "request": rows[0]}


@router.get("/marketplace/admin/listings")
def list_marketplace_admin_queue(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    _require_admin(db, owner_id)
    listings = _rows(db.table("creator_marketplace_listings").select(
        "id,product_id,owner_id,status,price_amount,currency,created_at"
    ).eq("status", "pending").order("created_at").limit(200).execute())
    if not listings:
        return {"success": True, "listings": []}
    product_ids = list({row["product_id"] for row in listings})
    owner_ids = list({row["owner_id"] for row in listings})
    products = _rows(db.table("creator_products").select(
        "id,title,description,product_type,is_sensitive,age_rating"
    ).in_("id", product_ids).execute())
    profiles = _rows(db.table("creator_profiles").select(
        "user_id,display_name"
    ).in_("user_id", owner_ids).execute())
    product_map = {str(row["id"]): row for row in products}
    profile_map = {str(row["user_id"]): row for row in profiles}
    items = []
    for listing in listings:
        product = product_map.get(str(listing["product_id"]))
        if product:
            items.append({"listing": listing, "product": product, "vendor": profile_map.get(str(listing["owner_id"]), {})})
    return {"success": True, "listings": items}


@router.patch("/marketplace/admin/listings/{listing_id}")
def review_marketplace_listing(
    listing_id: UUID,
    body: ListingReviewRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    _require_admin(db, owner_id)
    listing = _one(db, "creator_marketplace_listings", "id,status", id=str(listing_id))
    if not listing:
        raise HTTPException(status_code=404, detail="Marketplace listing not found.")
    if listing.get("status") != "pending":
        raise HTTPException(status_code=409, detail="Only pending listings can be reviewed.")
    rows = _rows(db.table("creator_marketplace_listings").update({
        "status": body.status
    }).eq("id", str(listing_id)).eq("status", "pending").select("*").execute())
    if not rows:
        raise HTTPException(status_code=409, detail="Marketplace decision was not saved.")
    return {"success": True, "listing": rows[0]}


__all__ = ["router"]
