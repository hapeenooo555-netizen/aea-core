"""Authenticated checkout-session creation for the creator marketplace.

This endpoint creates a pending sandbox order only. It does not collect money,
call ClickPesa, confirm payment, or grant a download entitlement.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID
import hashlib
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.database import get_supabase_service_client
from app.services.clickpesa_webhooks import verify_payload_checksum

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


@router.post("/webhooks/clickpesa", status_code=202)
async def receive_clickpesa_webhook(request: Request) -> dict[str, Any]:
    """Verify and durably inbox ClickPesa events; never mark orders paid here."""
    raw = await request.body()
    if len(raw) > 64 * 1024:
        raise HTTPException(status_code=413, detail="Webhook payload too large.")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook JSON.") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload.")

    checksum_secret = os.getenv("CLICKPESA_CHECKSUM_KEY", "").strip()
    if not checksum_secret or not verify_payload_checksum(checksum_secret, payload):
        raise HTTPException(status_code=401, detail="Invalid ClickPesa webhook checksum.")

    event_type = payload.get("event") or payload.get("eventType")
    data = payload.get("data")
    if event_type not in {"PAYMENT RECEIVED", "PAYMENT FAILED"} or not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Unsupported ClickPesa event.")

    try:
        order_id = UUID(str(data.get("orderReference", "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid order reference.") from exc

    provider_event_id = data.get("paymentReference") or data.get("paymentId") or data.get("id")
    if not isinstance(provider_event_id, str) or not provider_event_id.strip():
        raise HTTPException(status_code=400, detail="Missing provider payment reference.")

    amount_raw = data.get("collectedAmount")
    amount = None
    if amount_raw is not None:
        try:
            from decimal import Decimal, InvalidOperation
            parsed = Decimal(str(amount_raw))
            if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral_value():
                raise ValueError
            amount = int(parsed)
        except (InvalidOperation, ValueError):
            raise HTTPException(status_code=400, detail="Invalid collected amount.")

    currency = data.get("collectedCurrency")
    if currency is not None and not isinstance(currency, str):
        raise HTTPException(status_code=400, detail="Invalid collected currency.")

    client = get_supabase_service_client()
    if client is None:
        raise HTTPException(status_code=503, detail="Payment event storage is not configured.")

    digest = hashlib.sha256(raw).hexdigest()
    row = {
        "order_id": str(order_id),
        "provider": "clickpesa",
        "provider_event_id": provider_event_id.strip(),
        "event_type": event_type,
        "verification_status": "received",
        "amount": amount,
        "currency": currency,
        "payload_sha256": digest,
    }
    try:
        result = client.table("payment_events").upsert(
            row, on_conflict="provider,provider_event_id", ignore_duplicates=True
        ).execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not persist payment event.") from exc

    # A valid checksum proves payload integrity, not settlement. A separate
    # provider status query and exact order/amount/currency match are required.
    return {
        "accepted": True,
        "event_recorded": True,
        "event_type": event_type,
        "payment_reference": provider_event_id.strip(),
        "payment_status": "pending_verification",
        "order_status_changed": False,
        "entitlement_granted": False,
    }


__all__ = ["router"]
