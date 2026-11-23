"""First-party AEA affiliate-link issuance and public click redirect."""
from __future__ import annotations

import os
import secrets
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import RedirectResponse

from app import database as database_module
from app.dependencies import get_current_user_id, get_user_scoped_client

router = APIRouter(prefix="/store/affiliate", tags=["aea-affiliate"])
products_router = APIRouter(prefix="/store/products", tags=["public-marketplace"])


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    if isinstance(data, list):
        return data
    return [data] if isinstance(data, dict) else []


@products_router.get("/public/{slug}")
def get_public_marketplace_product(slug: str) -> dict[str, Any]:
    """Return a deliberately minimal public payload for an approved AEA listing."""
    if not slug or len(slug) > 180 or "/" in slug or ".." in slug:
        raise HTTPException(status_code=404, detail="Product not found.")
    client = database_module.supabase_client
    if client is None:
        raise HTTPException(status_code=503, detail="Marketplace product service is unavailable.")
    try:
        result = client.rpc("get_public_creator_product_by_slug", {"p_slug": slug}).execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Marketplace product service is unavailable.") from exc
    rows = _rows(result)
    if not rows:
        raise HTTPException(status_code=404, detail="Product not found or unavailable.")
    return {"success": True, "product": rows[0]}


@router.get("/earnings")
def get_my_affiliate_earnings(
    promoter_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Return only the authenticated promoter's attributed commission ledger."""
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is unavailable.")
    try:
        result = client.rpc("get_my_creator_affiliate_earnings", {}).execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Affiliate earnings are temporarily unavailable.") from exc
    rows = _rows(result)
    entries = [{
        "order_id": row.get("order_id"),
        "product_id": row.get("product_id"),
        "product_title": str(row.get("product_title") or "Digital product"),
        "product_type": str(row.get("product_type") or "digital_product"),
        "unit_amount": int(row.get("unit_amount") or 0),
        "currency": str(row.get("currency") or "TZS"),
        "commission_rate_bps": row.get("commission_rate_bps"),
        "commission_amount": int(row.get("commission_amount") or 0),
        "commission_status": str(row.get("commission_status") or "pending"),
        "created_at": row.get("created_at"),
    } for row in rows]
    payable = sum(x["commission_amount"] for x in entries if x["commission_status"] == "payable")
    paid = sum(x["commission_amount"] for x in entries if x["commission_status"] == "paid")
    pending = sum(x["commission_amount"] for x in entries if x["commission_status"] in {"pending", "approved"})
    return {
        "success": True,
        "earnings": entries,
        "summary": {
            "referral_order_count": len(entries),
            "payable_amount": payable,
            "paid_amount": paid,
            "pending_amount": pending,
            "currency": "TZS",
        },
        "notice": "Only commission entries attributed to your account are returned. Payable is not the same as paid out.",
    }



@router.post("/payout-requests", status_code=201)
def create_my_payout_request(
    payment_method: str = Body(..., embed=True),
    promoter_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Reserve all currently payable commissions for this authenticated promoter."""
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is unavailable.")
    try:
        rows = _rows(client.rpc("create_my_creator_payout_request", {
            "p_payment_method": payment_method,
        }).execute())
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Payout request could not be created. Check your payable balance and any active request.") from exc
    if not rows:
        raise HTTPException(status_code=409, detail="No payable commissions are available for payout.")
    return {"success": True, "payout_request": rows[0]}


@router.get("/payout-requests")
def list_my_payout_requests(
    promoter_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is unavailable.")
    try:
        rows = _rows(client.rpc("get_my_creator_payout_requests", {}).execute())
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Payout history is temporarily unavailable.") from exc
    return {"success": True, "payout_requests": rows}


@router.post("/requests/{request_id}/link", status_code=201)
def create_affiliate_link(
    request_id: UUID,
    request: Request,
    promoter_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Mint/retrieve a unique AEA referral URL after vendor approval."""
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is unavailable.")
    code = secrets.token_urlsafe(24)
    try:
        result = client.rpc("create_creator_affiliate_link", {
            "p_request_id": str(request_id),
            "p_code": code,
        }).execute()
    except Exception as exc:
        # Do not leak database details or tokens into API responses/logs.
        raise HTTPException(status_code=409, detail="An affiliate link could not be issued. Confirm the promotion request is approved and the product is active.") from exc
    rows = _rows(result)
    if not rows:
        raise HTTPException(status_code=409, detail="AEA did not confirm affiliate link creation.")
    saved = rows[0]
    saved_code = saved.get("referral_code")
    if not saved_code:
        raise HTTPException(status_code=502, detail="AEA returned an incomplete affiliate link record.")
    affiliate_url = str(request.url_for("resolve_affiliate_redirect", code=saved_code))
    return {
        "success": True,
        "affiliate_link": {
            "id": saved.get("id"),
            "product_id": saved.get("product_id"),
            "status": saved.get("status"),
            "url": affiliate_url,
            "created_at": saved.get("created_at"),
        },
        "notice": "This AEA link redirects to the AEA product page and records referral clicks. Checkout and commission settlement are separate capabilities.",
    }


@router.get("/r/{code}", name="resolve_affiliate_redirect")
def resolve_affiliate_redirect(code: str) -> RedirectResponse:
    """Record a valid referral click and redirect to the AEA-hosted product page."""
    if not code or len(code) < 20 or len(code) > 80:
        raise HTTPException(status_code=404, detail="Affiliate link not found.")
    client = database_module.supabase_client
    if client is None:
        raise HTTPException(status_code=503, detail="Marketplace referral service is unavailable.")
    try:
        result = client.rpc("resolve_creator_affiliate_link", {"p_code": code}).execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Marketplace referral service is unavailable.") from exc
    rows = _rows(result)
    if not rows or not rows[0].get("product_slug"):
        raise HTTPException(status_code=404, detail="Affiliate link is invalid, revoked, or the product is unavailable.")
    hub_url = os.getenv("AEA_HUB_PUBLIC_URL", "").strip().rstrip("/")
    if not hub_url.startswith("https://"):
        raise HTTPException(status_code=503, detail="AEA product storefront is not configured.")
    slug = str(rows[0]["product_slug"])
    if not slug or "/" in slug or ".." in slug:
        raise HTTPException(status_code=404, detail="Product page not found.")
    destination = f"{hub_url}/products/{slug}?ref={code}"
    return RedirectResponse(url=destination, status_code=302)


__all__ = ["router"]
