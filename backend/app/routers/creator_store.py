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
