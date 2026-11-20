"""Creator-owned digital product listings and safe social distribution requests.

Distribution requests are persisted for review/connection checks. This router does
not publish directly to social networks; provider adapters and approval execution
must complete that separately.
"""
from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client

router = APIRouter(prefix="/store/products", tags=["creator-store"])

Destination = Literal["pinterest", "facebook", "instagram", "tiktok", "youtube"]
# Only Pinterest and Meta are currently integrated in AEA Core. This is not
# equivalent to a connected account or permission to publish.
INTEGRATED_DESTINATIONS = {"pinterest", "facebook", "instagram"}


class ProductCreateRequest(BaseModel):
    asset_id: UUID
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=20, max_length=5000)
    product_type: Literal["ebook", "video", "course", "template", "workbook", "guide", "digital_pack"] = "guide"
    price_amount: int = Field(gt=0, le=100000000)
    currency: Literal["TZS"] = "TZS"
    model_config = ConfigDict(extra="forbid")


class ProductStatusRequest(BaseModel):
    status: Literal["listed", "archived"]
    model_config = ConfigDict(extra="forbid")


class DistributionRequest(BaseModel):
    destination: Destination
    model_config = ConfigDict(extra="forbid")

class MarketplaceOptInRequest(BaseModel):
    enabled: bool
    model_config = ConfigDict(extra="forbid")


class PromotionRequestCreate(BaseModel):
    listing_id: UUID
    message: str = Field(default="", max_length=2000)
    model_config = ConfigDict(extra="forbid")


class PromotionDecision(BaseModel):
    status: Literal["approved", "rejected"]
    model_config = ConfigDict(extra="forbid")


def _db(client: Any) -> Any:
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is not configured.")
    return client


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    return data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])


def _slug(value: str) -> str:
    value = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return value[:150] or "aea-digital-product"


@router.get("/mine")
def list_my_products(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    result = db.table("creator_products").select(
        "id,asset_id,title,slug,description,product_type,price_amount,currency,status,visibility,is_sensitive,age_rating,created_at,updated_at"
    ).eq("owner_id", owner_id).order("created_at", desc=True).limit(100).execute()
    return {"success": True, "products": _rows(result)}


@router.get("/mine/distribution")
def list_my_distribution_requests(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    result = db.table("creator_distribution_jobs").select(
        "id,product_id,destination,status,error,created_at,updated_at"
    ).eq("owner_id", owner_id).order("created_at", desc=True).limit(200).execute()
    return {"success": True, "jobs": _rows(result)}


@router.post("/{product_id}/distribution", status_code=status.HTTP_202_ACCEPTED)
def request_product_distribution(
    product_id: UUID,
    body: DistributionRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Persist an owner-authorized distribution request without publishing."""
    db = _db(client)
    product_result = db.table("creator_products").select("id,owner_id,status,asset_id").eq(
        "id", str(product_id)
    ).eq("owner_id", owner_id).limit(1).execute()
    products = _rows(product_result)
    if not products:
        raise HTTPException(status_code=404, detail="Product listing not found.")
    if products[0].get("status") == "archived":
        raise HTTPException(status_code=409, detail="Archived products cannot be distributed.")

    existing_result = db.table("creator_distribution_jobs").select(
        "id,product_id,destination,status,error,created_at"
    ).eq("owner_id", owner_id).eq("product_id", str(product_id)).eq(
        "destination", body.destination
    ).limit(1).execute()
    existing = _rows(existing_result)
    if existing:
        return {
            "success": True,
            "created": False,
            "job": existing[0],
            "published": False,
            "notice": "An existing distribution request was returned; no duplicate was created.",
        }

    supported = body.destination in INTEGRATED_DESTINATIONS
    payload = {
        "owner_id": owner_id,
        "product_id": str(product_id),
        "destination": body.destination,
        "status": "pending_approval" if supported else "draft",
        "approval_required": True,
        "error": None if supported else (
            f"{body.destination.title()} OAuth and publishing adapter are not yet integrated in AEA Core."
        ),
    }
    try:
        saved = db.table("creator_distribution_jobs").insert(payload).execute()
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Distribution request could not be saved.") from exc
    rows = _rows(saved)
    if not rows:
        raise HTTPException(status_code=502, detail="Database did not confirm distribution request creation.")
    job = rows[0]
    return {
        "success": True,
        "created": True,
        "job": job,
        "published": False,
        "notice": (
            "Request saved for human approval. A connected, authorized destination is still required before publishing."
            if supported else
            "Request saved as a draft with a capability warning. This destination needs its official OAuth and publishing integration."
        ),
    }




@router.post("/{product_id}/marketplace/listing", status_code=status.HTTP_202_ACCEPTED)
def submit_marketplace_listing(product_id: UUID, owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    """Submit an explicitly opted-in, listed product for marketplace review."""
    db = _db(client)
    products = _rows(db.table("creator_products").select(
        "id,owner_id,status,marketplace_enabled,price_amount,currency"
    ).eq("id", str(product_id)).eq("owner_id", owner_id).limit(1).execute())
    if not products:
        raise HTTPException(status_code=404, detail="Product listing not found.")
    product = products[0]
    if product.get("status") != "listed" or not product.get("marketplace_enabled"):
        raise HTTPException(status_code=409, detail="List the product and enable Marketplace discovery first.")
    existing = _rows(db.table("creator_marketplace_listings").select(
        "id,product_id,owner_id,status,price_amount,currency"
    ).eq("product_id", str(product_id)).limit(1).execute())
    if existing:
        return {"success": True, "created": False, "listing": existing[0], "notice": "Marketplace listing already exists."}
    try:
        saved = db.table("creator_marketplace_listings").insert({
            "product_id": str(product_id), "owner_id": owner_id, "status": "pending",
            "price_amount": product["price_amount"], "currency": product["currency"]
        }).execute()
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Marketplace listing request could not be saved.") from exc
    rows = _rows(saved)
    if not rows:
        raise HTTPException(status_code=502, detail="Marketplace listing save was not confirmed.")
    return {"success": True, "created": True, "listing": rows[0], "notice": "Listing submitted for marketplace review."}


@router.get("/marketplace")
def browse_marketplace(owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    """Browse approved listings; RLS filters the associated product."""
    db = _db(client)
    listings = _rows(db.table("creator_marketplace_listings").select(
        "id,product_id,owner_id,status,price_amount,currency,created_at"
    ).eq("status", "approved").order("created_at", desc=True).limit(100).execute())
    output = []
    for listing in listings:
        products = _rows(db.table("creator_products").select(
            "id,title,description,product_type,price_amount,currency,age_rating,is_sensitive"
        ).eq("id", listing["product_id"]).eq("status", "listed").eq("marketplace_enabled", True).limit(1).execute())
        profiles = _rows(db.table("creator_profiles").select(
            "user_id,display_name"
        ).eq("user_id", listing["owner_id"]).eq("is_public", True).limit(1).execute())
        if products and profiles and str(listing["owner_id"]) != owner_id:
            output.append({"listing": listing, "product": products[0], "vendor": profiles[0]})
    return {"success": True, "listings": output}


@router.post("/marketplace/requests", status_code=status.HTTP_201_CREATED)
def create_promotion_request(body: PromotionRequestCreate, promoter_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    db = _db(client)
    listings = _rows(db.table("creator_marketplace_listings").select(
        "id,product_id,owner_id,status"
    ).eq("id", str(body.listing_id)).eq("status", "approved").limit(1).execute())
    if not listings:
        raise HTTPException(status_code=404, detail="Approved marketplace listing not found.")
    listing = listings[0]
    if str(listing["owner_id"]) == promoter_id:
        raise HTTPException(status_code=422, detail="You cannot request promotion for your own product.")
    products = _rows(db.table("creator_products").select("id,status,marketplace_enabled").eq(
        "id", listing["product_id"]).eq("status", "listed").eq("marketplace_enabled", True).limit(1).execute())
    if not products:
        raise HTTPException(status_code=404, detail="Marketplace product is not available.")
    existing = _rows(db.table("creator_promotion_requests").select(
        "id,listing_id,product_id,owner_id,promoter_id,status,message,created_at"
    ).eq("listing_id", str(body.listing_id)).eq("promoter_id", promoter_id).limit(1).execute())
    if existing:
        return {"success": True, "created": False, "request": existing[0]}
    try:
        saved = db.table("creator_promotion_requests").insert({
            "listing_id": str(body.listing_id), "product_id": listing["product_id"],
            "owner_id": listing["owner_id"], "promoter_id": promoter_id,
            "message": body.message.strip(), "status": "pending"
        }).execute()
    except Exception as exc:
        raise HTTPException(status_code=403, detail="Promotion request was not accepted by database policy.") from exc
    rows = _rows(saved)
    if not rows:
        raise HTTPException(status_code=409, detail="Promotion request save was not confirmed.")
    return {"success": True, "created": True, "request": rows[0]}


@router.get("/marketplace/requests/mine")
def list_my_promotion_requests(owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    rows = _rows(_db(client).table("creator_promotion_requests").select(
        "id,listing_id,product_id,owner_id,promoter_id,status,message,created_at,updated_at"
    ).or_(f"owner_id.eq.{owner_id},promoter_id.eq.{owner_id}").order("created_at", desc=True).limit(200).execute())
    return {"success": True, "requests": rows}


@router.patch("/marketplace/requests/{request_id}")
def review_promotion_request(request_id: UUID, body: PromotionDecision, owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    db = _db(client)
    existing = _rows(db.table("creator_promotion_requests").select("id,status").eq(
        "id", str(request_id)).eq("owner_id", owner_id).limit(1).execute())
    if not existing:
        raise HTTPException(status_code=404, detail="Promotion request not found.")
    if existing[0]["status"] != "pending":
        raise HTTPException(status_code=409, detail="Only pending requests can be reviewed.")
    saved = db.table("creator_promotion_requests").update({"status": body.status}).eq(
        "id", str(request_id)).eq("owner_id", owner_id).eq("status", "pending").select(
        "id,status,owner_id,promoter_id,product_id,listing_id").execute()
    rows = _rows(saved)
    if not rows:
        raise HTTPException(status_code=409, detail="Promotion request was not updated.")
    return {"success": True, "request": rows[0]}


@router.patch("/{product_id}/marketplace")
def set_marketplace_opt_in(product_id: UUID, body: MarketplaceOptInRequest, owner_id: str = Depends(get_current_user_id), client: Any = Depends(get_user_scoped_client)) -> dict[str, Any]:
    db = _db(client)
    existing = _rows(db.table("creator_products").select("id,status").eq(
        "id", str(product_id)).eq("owner_id", owner_id).limit(1).execute())
    if not existing:
        raise HTTPException(status_code=404, detail="Product listing not found.")
    if body.enabled and existing[0].get("status") != "listed":
        raise HTTPException(status_code=409, detail="List the product before enabling marketplace discovery.")
    rows = _rows(db.table("creator_products").update({"marketplace_enabled": body.enabled}).eq(
        "id", str(product_id)).eq("owner_id", owner_id).select("id,marketplace_enabled,status").execute())
    if not rows:
        raise HTTPException(status_code=409, detail="Marketplace preference was not saved.")
    return {"success": True, "product": rows[0]}


@router.get("")
def browse_products(
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Browse listed creator products. RLS hides unlisted and private records."""
    db = _db(client)
    result = db.table("creator_products").select(
        "id,owner_id,title,slug,description,product_type,price_amount,currency,status,created_at"
    ).eq("status", "listed").order("created_at", desc=True).limit(100).execute()
    return {"success": True, "products": _rows(result)}


@router.post("", status_code=status.HTTP_201_CREATED)
def create_product_listing(
    body: ProductCreateRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Create a private draft listing from a content asset owned by the caller."""
    db = _db(client)
    asset_response = db.table("content_asset_versions").select(
        "asset_id,owner_id,title,version,status"
    ).eq("owner_id", owner_id).eq("asset_id", str(body.asset_id)).order(
        "version", desc=True
    ).limit(1).execute()
    assets = _rows(asset_response)
    if not assets:
        raise HTTPException(status_code=404, detail="Saved content asset not found.")
    asset = assets[0]
    if asset.get("status") not in {"draft", "approved"}:
        raise HTTPException(status_code=409, detail="This content asset is not eligible for product listing.")

    slug = _slug(body.title) + "-" + uuid4().hex[:8]
    payload = {
        "owner_id": owner_id,
        "asset_id": str(body.asset_id),
        "title": body.title.strip(),
        "slug": slug,
        "description": body.description.strip(),
        "product_type": body.product_type,
        "price_amount": body.price_amount,
        "currency": body.currency,
        "status": "draft",
    }
    try:
        saved = db.table("creator_products").insert(payload).execute()
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Product listing could not be saved.") from exc
    rows = _rows(saved)
    if not rows:
        raise HTTPException(status_code=502, detail="Database did not confirm product listing creation.")
    return {"success": True, "product": rows[0], "checkout_enabled": False, "delivery_enabled": False}


@router.patch("/{product_id}/status")
def update_product_status(
    product_id: UUID,
    body: ProductStatusRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Allow owner to list or archive a product; never activates checkout."""
    db = _db(client)
    existing = db.table("creator_products").select("id,status").eq(
        "id", str(product_id)
    ).eq("owner_id", owner_id).limit(1).execute()
    if not _rows(existing):
        raise HTTPException(status_code=404, detail="Product listing not found.")
    updated = db.table("creator_products").update({"status": body.status}).eq(
        "id", str(product_id)
    ).eq("owner_id", owner_id).select("*").execute()
    rows = _rows(updated)
    if not rows:
        raise HTTPException(status_code=409, detail="Product listing status was not updated.")
    return {
        "success": True,
        "product": rows[0],
        "checkout_enabled": False,
        "delivery_enabled": False,
        "notice": "Listing visibility only. Checkout and paid delivery are not enabled.",
    }


__all__ = ["router"]
