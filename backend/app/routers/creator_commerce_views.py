"""Read-only buyer and seller commerce views, scoped by Supabase RLS."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_current_user_id, get_user_scoped_client

router = APIRouter(prefix="/commerce", tags=["creator-commerce"])


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    return data if isinstance(data, list) else []


@router.get("/orders")
def list_my_orders(
    user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
    limit: int = Query(default=25, ge=1, le=100),
) -> dict[str, Any]:
    """List orders visible to the signed-in buyer or seller under RLS."""
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is not configured.")
    try:
        result = (
            client.table("marketplace_orders")
            .select("id,buyer_id,seller_id,status,currency,subtotal_amount,discount_amount,total_amount,provider,created_at,paid_at")
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        orders = _rows(result)
        # RLS is the access boundary; this filter is a defensive response check.
        visible = [row for row in orders if user_id in {row.get("buyer_id"), row.get("seller_id")}]
        return {"orders": visible, "count": len(visible)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Orders could not be loaded.") from exc


@router.get("/seller/ledger")
def get_seller_ledger(
    user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
    limit: int = Query(default=50, ge=1, le=100),
) -> dict[str, Any]:
    """Return the authenticated seller's ledger entries; no balance is inferred."""
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is not configured.")
    try:
        result = (
            client.table("seller_ledger_entries")
            .select("id,order_id,entry_type,amount,currency,metadata,created_at")
            .eq("seller_id", user_id)
            .order("created_at", desc=True)
            .limit(limit)
            .execute()
        )
        entries = _rows(result)
        return {"entries": entries, "count": len(entries)}
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Seller ledger could not be loaded.") from exc


__all__ = ["router"]
