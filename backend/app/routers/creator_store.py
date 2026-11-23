"""Creator-owned digital product listings for the AEA storefront.

This phase creates product listings only. It does not accept payments or deliver
paid files; those actions must wait for a verified payment provider and secure
order-scoped delivery implementation.
"""
from __future__ import annotations

import re
import os
import uuid
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


class ProductFileUploadRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    mime_type: Literal["application/pdf", "application/epub+zip", "application/zip", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"]
    size_bytes: int = Field(gt=0, le=52428800)
    model_config = ConfigDict(extra="forbid")


class ProductFileCompleteRequest(BaseModel):
    file_id: UUID
    model_config = ConfigDict(extra="forbid")


def _storage(client: Any) -> Any:
    storage = getattr(client, "storage", None)
    if storage is None:
        raise HTTPException(status_code=503, detail="Private product storage is not configured.")
    return storage


def _storage_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    if hasattr(value, "__dict__"):
        return vars(value)
    return {}


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
        "id,asset_id,title,slug,description,product_type,price_amount,currency,status,completion_status,created_at,updated_at"
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
    # A nested core_content object may hold the structured chapter list.
    nested_chapters = core.get("chapters") or core.get("chapter_content") if isinstance(core, dict) else None
    chapters = content.get("chapters") or content.get("chapter_content") or content.get("modules") or nested_chapters
    if isinstance(chapters, list) and chapters:
        readable = [
            item for item in chapters
            if isinstance(item, dict)
            and len(str(item.get("body") or item.get("content") or item.get("text") or "").strip()) >= 200
        ]
        return len(readable) == len(chapters)
    # Single-body digital guides are allowed only when the actual text is present.
    if product_type in {"ebook", "guide", "workbook"} and isinstance(core, str):
        return len(core.strip()) >= 300
    return False


@router.post("/{product_id}/files/upload-url", status_code=status.HTTP_201_CREATED)
def create_product_file_upload(
    product_id: UUID,
    body: ProductFileUploadRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Create a short-lived signed upload URL into the private product bucket."""
    db = _db(client)
    products = _rows(db.table("creator_products").select("id,owner_id,status").eq(
        "id", str(product_id)
    ).eq("owner_id", owner_id).limit(1).execute())
    if not products:
        raise HTTPException(status_code=404, detail="Product not found.")
    if products[0].get("status") != "draft":
        raise HTTPException(status_code=409, detail="Only draft products can receive a new file.")
    safe_name = re.sub(r"[^A-Za-z0-9._-]+", "-", body.filename).strip(".-")[:180]
    if not safe_name:
        raise HTTPException(status_code=422, detail="Invalid filename.")
    extension = os.path.splitext(safe_name)[1].lower()
    allowed = {
        "application/pdf": {".pdf"},
        "application/epub+zip": {".epub"},
        "application/zip": {".zip"},
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {".docx"},
    }
    if extension not in allowed[body.mime_type]:
        raise HTTPException(status_code=422, detail="Filename extension does not match the declared file type.")
    path = f"{owner_id}/{product_id}/{uuid.uuid4().hex}-{safe_name}"
    try:
        response = _storage(client).from_("aea-product-files").create_signed_upload_url(path)
        payload = _storage_payload(response)
        signed_url = payload.get("signed_url") or payload.get("signedURL") or payload.get("url")
        token = payload.get("token")
        if not signed_url and not token:
            raise ValueError("Storage did not return a signed upload credential.")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not create a private upload URL.") from exc
    saved = _rows(db.table("creator_product_files").insert({
        "product_id": str(product_id),
        "owner_id": owner_id,
        "bucket_id": "aea-product-files",
        "object_path": path,
        "original_filename": safe_name,
        "mime_type": body.mime_type,
        "size_bytes": body.size_bytes,
        "status": "pending",
    }).execute())
    if not saved:
        raise HTTPException(status_code=502, detail="Upload record could not be saved.")
    return {
        "success": True,
        "file": {"id": saved[0].get("id"), "filename": safe_name, "mime_type": body.mime_type, "size_bytes": body.size_bytes},
        "upload": {"signed_url": signed_url, "token": token, "path": path, "bucket": "aea-product-files"},
        "expires_in_seconds": 7200,
        "notice": "Private upload only. The file is not yet verified or available to buyers.",
    }


@router.post("/{product_id}/files/complete")
def complete_product_file_upload(
    product_id: UUID,
    body: ProductFileCompleteRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Verify the uploaded object exists with the expected size before marking it ready."""
    db = _db(client)
    files = _rows(db.table("creator_product_files").select(
        "id,product_id,owner_id,bucket_id,object_path,original_filename,mime_type,size_bytes,status"
    ).eq("id", str(body.file_id)).eq("product_id", str(product_id)).eq("owner_id", owner_id).limit(1).execute())
    if not files:
        raise HTTPException(status_code=404, detail="Product file not found.")
    file_row = files[0]
    if file_row.get("status") == "ready":
        return {"success": True, "file": file_row, "duplicate": True}
    path = str(file_row.get("object_path") or "")
    folder, _, filename = path.rpartition("/")
    try:
        entries = _storage(client).from_("aea-product-files").list(folder, {"search": filename})
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Uploaded object could not be verified in private storage.") from exc
    match = next((item for item in (entries or []) if item.get("name") == filename), None)
    metadata = (match or {}).get("metadata") or {}
    actual_size = metadata.get("size") or metadata.get("contentLength")
    if not match or (actual_size is not None and int(actual_size) != int(file_row.get("size_bytes") or 0)):
        raise HTTPException(status_code=409, detail="Uploaded file is missing or its size does not match the upload declaration.")
    updated = _rows(db.table("creator_product_files").update({"status": "ready"}).eq(
        "id", str(body.file_id)
    ).eq("owner_id", owner_id).select("*").execute())
    if not updated:
        raise HTTPException(status_code=502, detail="Verified file status could not be saved.")
    return {"success": True, "file": updated[0], "buyer_delivery_enabled": False}


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
