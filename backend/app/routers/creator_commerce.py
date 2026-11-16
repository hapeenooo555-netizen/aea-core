"""Authenticated checkout-session creation for the creator marketplace.

This endpoint creates a pending sandbox order only. It does not collect money,
call ClickPesa, confirm payment, or grant a download entitlement.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client

router = APIRouter(prefix="/commerce", tags=["creator-commerce"])


class CheckoutSessionRequest(BaseModel):
    product_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    model_config = ConfigDict(extra="forbid")


@router.post("/checkout-sessions", status_code=201)
def create_checkout_session(
    body: CheckoutSessionRequest,
    buyer_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Create an idempotent pending sandbox order for a listed product."""
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is not configured.")

    try:
        result = client.rpc(
            "create_marketplace_checkout",
            {
                "p_product_id": str(body.product_id),
                "p_idempotency_key": body.idempotency_key,
            },
        ).execute()
        data = getattr(result, "data", None)
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict) or not data.get("order_id"):
            raise HTTPException(status_code=502, detail="Checkout order was not confirmed by the database.")
        # The buyer identity is derived from the verified JWT; never trust a browser-supplied buyer ID.
        return {
            "success": True,
            "checkout": data,
            "payment_status": "pending",
            "checkout_url": None,
            "live_payment_enabled": False,
            "message": "Sandbox order created. No payment has been collected and no download is available.",
        }
    except HTTPException:
        raise
    except Exception as exc:
        # Avoid returning database/provider internals to the browser.
        raise HTTPException(status_code=409, detail="Checkout session could not be created.") from exc


__all__ = ["router"]
