"""Creator-owned digital product listings for the AEA storefront.

This phase creates product listings only. It does not accept payments or deliver
paid files; those actions must wait for a verified payment provider and secure
order-scoped delivery implementation.
"""
from __future__ import annotations

import re
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client

router = APIRouter(prefix="/store/products", tags=["creator-store"])


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
        "id,asset_id,title,slug,description,product_type,price_amount,currency,status,created_at,updated_at"
    ).eq("owner_id", owner_id).order("created_at", desc=True).limit(100).execute()
    return {"success": True, "products": _rows(result)}


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
        "asset_id,owner_id,title,content,version,status"
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


def _asset_has_finished_content(product_type: str, asset: dict[str, Any]) -> bool:
    """Require approved, substantive content before a product can be finished."""
    if asset.get("status") != "approved":
        return False
    content = asset.get("content")
    if not isinstance(content, dict):
        return False
    core = content.get("core_content")
    if isinstance(core, str) and len(core.strip()) >= 300:
        return True
    if isinstance(core, dict):
        core_text = " ".join(str(value) for value in core.values() if isinstance(value, (str, int, float)))
        if len(core_text.strip()) >= 300:
            return True
    # Long-form ebooks/courses need actual readable chapter/module bodies,
    # not only a title or table of contents.
    if product_type in {"ebook", "course", "workbook", "guide"}:
        chapters = content.get("chapters") or content.get("chapter_content") or content.get("modules")
        if isinstance(chapters, list) and chapters:
            readable = [item for item in chapters if isinstance(item, dict) and len(str(item.get("body") or item.get("content") or "").strip()) >= 200]
            return len(readable) == len(chapters)
    return False


@router.get("/{product_id}/reader")
def read_product_preview(
    product_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Return the owner's saved, approved ebook/course text for private preview only."""
    db = _db(client)
    products = _rows(db.table("creator_products").select(
        "id,owner_id,asset_id,title,product_type,completion_status"
    ).eq("id", str(product_id)).eq("owner_id", owner_id).limit(1).execute())
    if not products:
        raise HTTPException(status_code=404, detail="Product not found.")
    product = products[0]
    if product.get("product_type") not in {"ebook", "course", "workbook", "guide"}:
        raise HTTPException(status_code=422, detail="This product type does not use the text reader.")
    assets = _rows(db.table("content_asset_versions").select(
        "asset_id,owner_id,title,content,status,version"
    ).eq("owner_id", owner_id).eq("asset_id", str(product.get("asset_id"))).order(
        "version", desc=True
    ).limit(1).execute())
    if not assets or assets[0].get("status") != "approved":
        raise HTTPException(status_code=409, detail="Only approved saved content can be opened in the reader.")
    content = assets[0].get("content")
    if not isinstance(content, dict):
        raise HTTPException(status_code=409, detail="This asset does not contain readable structured content.")
    core = content.get("core_content")
    chapters = content.get("chapters") or content.get("chapter_content")
    if not chapters and isinstance(core, dict):
        chapters = core.get("chapters") or core.get("chapter_content")
    if not chapters and isinstance(core, str):
        chapters = [{"title": product.get("title") or assets[0].get("title"), "body": core}]
    if not isinstance(chapters, list) or not chapters:
        raise HTTPException(status_code=409, detail="No readable chapter content is saved for this product yet.")
    readable = []
    for index, chapter in enumerate(chapters, start=1):
        if isinstance(chapter, str):
            title, body = f"Chapter {index}", chapter
        elif isinstance(chapter, dict):
            title = str(chapter.get("title") or f"Chapter {index}")
            body = chapter.get("body") or chapter.get("content") or chapter.get("text") or ""
        else:
            continue
        if isinstance(body, str) and body.strip():
            readable.append({"number": index, "title": title, "body": body})
    if not readable:
        raise HTTPException(status_code=409, detail="No readable chapter text is available.")
    return {
        "success": True,
        "access": "owner_preview_only",
        "product": {"id": product.get("id"), "title": product.get("title"), "product_type": product.get("product_type")},
        "chapters": readable,
        "notice": "Private creator preview. This endpoint does not grant buyer access or paid delivery.",
    }


@router.post("/{product_id}/finish")
def finish_product(
    product_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Mark a product finished only after its latest owned content asset is approved and substantive."""
    db = _db(client)
    existing = db.table("creator_products").select(
        "id,owner_id,asset_id,product_type,status,completion_status"
    ).eq("id", str(product_id)).eq("owner_id", owner_id).limit(1).execute()
    products = _rows(existing)
    if not products:
        raise HTTPException(status_code=404, detail="Product listing not found.")
    product = products[0]
    asset_response = db.table("content_asset_versions").select(
        "asset_id,owner_id,title,content_type,content,status,version"
    ).eq("owner_id", owner_id).eq("asset_id", str(product.get("asset_id"))).order(
        "version", desc=True
    ).limit(1).execute()
    assets = _rows(asset_response)
    if not assets or not _asset_has_finished_content(str(product.get("product_type") or ""), assets[0]):
        raise HTTPException(
            status_code=409,
            detail="Product is not ready. Approve its latest saved content and ensure the actual product text or chapter bodies are complete before finishing.",
        )
    rows = _rows(db.table("creator_products").update({
        "completion_status": "finished"
    }).eq("id", str(product_id)).eq("owner_id", owner_id).select("*").execute())
    if not rows:
        raise HTTPException(status_code=409, detail="Product completion could not be saved.")
    return {
        "success": True,
        "product": rows[0],
        "completion_status": "finished",
        "checkout_enabled": False,
        "delivery_enabled": False,
        "notice": "Product content is marked finished. Checkout and paid delivery are still disabled.",
    }


@router.patch("/{product_id}/status")
def update_product_status(
    product_id: UUID,
    body: ProductStatusRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Allow owner to list or archive a product; never activates checkout."""
    db = _db(client)
    existing = db.table("creator_products").select("id,status,completion_status").eq(
        "id", str(product_id)
    ).eq("owner_id", owner_id).limit(1).execute()
    existing_rows = _rows(existing)
    if not existing_rows:
        raise HTTPException(status_code=404, detail="Product listing not found.")
    if body.status == "listed" and existing_rows[0].get("completion_status") != "finished":
        raise HTTPException(status_code=409, detail="Finish and validate the product before listing it for sale.")
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
