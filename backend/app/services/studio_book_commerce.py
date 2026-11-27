"""Prepare an owned Studio e-book as an unlisted Creator Store product."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from .readable_chapters import readable_chapters


def prepare_book_product(
    source: dict[str, Any], payload: dict[str, Any], owner_id: str, client: Any,
) -> dict[str, Any]:
    from app.routers.creator_store import ProductCreateRequest, create_product_listing

    chapters = readable_chapters(source["content"], minimum_length=200)
    if not chapters:
        return {"success": False, "error": "The e-book needs readable chapters before creating a product draft."}
    title = source["title"].strip()[:180]
    chapter_titles = " / ".join(chapter["title"] for chapter in chapters)
    price = int(payload["price_tzs"])
    result = create_product_listing(ProductCreateRequest(
        asset_id=UUID(source["asset_id"]),
        expected_asset_version=source["version"],
        title=title,
        description=f"{title}. Chapters: {chapter_titles}"[:5000],
        product_type="ebook",
        price_amount=price,
    ), owner_id, client)
    product = result["product"]
    if product.get("owner_id") != owner_id or not _is_matching_private_draft(product, source["asset_id"], price):
        return {
            "success": False, "waiting_input": True,
            "message": "This book already has a product with different settings. Review it in My Store.",
        }
    return {
        "success": True, "status": "private_product_prepared", "product_id": product["id"],
        "asset_id": source["asset_id"], "price_tzs": price, "reused": bool(result.get("duplicate")),
    }


def verify_book_product(prepared: dict[str, Any], owner_id: str, client: Any) -> dict[str, Any]:
    rows = client.table("creator_products").select(
        "id,owner_id,asset_id,product_type,title,description,price_amount,currency,status,visibility,marketplace_enabled"
    ).eq("owner_id", owner_id).eq("id", prepared["product_id"]).limit(1).execute().data or []
    for row in rows:
        if (
            row.get("owner_id") == owner_id and row.get("id") == prepared["product_id"]
            and _is_matching_private_draft(row, prepared["asset_id"], prepared["price_tzs"])
            and row.get("title") and row.get("description")
        ):
            return {
                **prepared, "status": "private_product_draft_verified",
                "title": row["title"], "verification": "OWNER_SCOPED_PRODUCT_READBACK",
            }
    return {"success": False, "error": "The private product draft could not be verified.", "retryable": True}


def _is_matching_private_draft(row: dict[str, Any], asset_id: str, price: int) -> bool:
    return (
        row.get("asset_id") == asset_id and row.get("product_type") == "ebook"
        and row.get("status") == "draft" and row.get("visibility") == "private"
        and row.get("marketplace_enabled") is False
        and row.get("price_amount") == price and row.get("currency") == "TZS"
    )
