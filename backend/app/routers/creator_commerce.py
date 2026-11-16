"""Authenticated creator-marketplace checkout and payment notifications."""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any
from uuid import UUID
import hashlib
import json
import os

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.database import get_supabase_service_client
from app.services.clickpesa_reconciliation import (
    PaymentReconciliationError,
    select_verified_success,
)
from app.services.clickpesa_client import (
    ClickPesaAPIError,
    ClickPesaClient,
    ClickPesaConfigurationError,
)
from app.services.clickpesa_webhooks import verify_payload_checksum

router = APIRouter(prefix="/commerce", tags=["creator-commerce"])


class CheckoutSessionRequest(BaseModel):
    product_id: UUID
    idempotency_key: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$")
    customer_name: str = Field(min_length=2, max_length=120)
    customer_email: str = Field(min_length=5, max_length=254, pattern=r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
    customer_phone: str = Field(min_length=9, max_length=20, pattern=r"^\+?[0-9]{9,20}$")
    model_config = ConfigDict(extra="forbid")


@router.post("/checkout-sessions", status_code=201)
def create_checkout_session(
    body: CheckoutSessionRequest,
    buyer_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Create an order and, only when explicitly enabled, a hosted ClickPesa link."""
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is not configured.")

    try:
        result = client.rpc(
            "create_marketplace_checkout",
            {"p_product_id": str(body.product_id), "p_idempotency_key": body.idempotency_key},
        ).execute()
        data = getattr(result, "data", None)
        if isinstance(data, list):
            data = data[0] if data else None
        if not isinstance(data, dict) or not data.get("order_id"):
            raise HTTPException(status_code=502, detail="Checkout order was not confirmed by the database.")
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=409, detail="Checkout session could not be created.") from exc

    if os.getenv("CLICKPESA_LIVE_ENABLED", "").strip().lower() != "true":
        return {
            "success": True, "checkout": data, "payment_status": "pending",
            "checkout_url": None, "live_payment_enabled": False,
            "message": "Sandbox order created. No payment has been collected and no download is available.",
        }

    service = get_supabase_service_client()
    if service is None:
        raise HTTPException(status_code=503, detail="Payment checkout is not configured.")
    order_id = str(data["order_id"])
    try:
        order_result = service.table("marketplace_orders").select(
            "id,buyer_id,status,total_amount,currency,provider,provider_checkout_url"
        ).eq("id", order_id).limit(1).execute()
        orders = getattr(order_result, "data", None) or []
        order = orders[0] if orders else None
        if not order or order.get("buyer_id") != buyer_id or order.get("status") != "pending":
            raise HTTPException(status_code=409, detail="Order is not eligible for payment.")
        if order.get("provider") == "clickpesa":
            existing_url = order.get("provider_checkout_url")
            if isinstance(existing_url, str) and existing_url.startswith("https://"):
                return {
                    "success": True, "checkout": {**data, "provider": "clickpesa"},
                    "payment_status": "pending", "checkout_url": existing_url,
                    "live_payment_enabled": True,
                    "message": "Existing hosted checkout reused; no additional link was generated.",
                }
            raise HTTPException(status_code=409, detail="ClickPesa checkout is already initialized; manual review is required.")
        if order.get("provider") != "test":
            raise HTTPException(status_code=409, detail="Order has an unsupported payment provider state.")

        item_result = service.table("marketplace_order_items").select(
            "product_title"
        ).eq("order_id", order_id).limit(1).execute()
        items = getattr(item_result, "data", None) or []
        title = str(items[0].get("product_title", "AEA Marketplace product")) if items else "AEA Marketplace product"

        # Claim the provider transition before the external API call. This prevents
        # concurrent retries from creating multiple independently payable links.
        claim_result = service.table("marketplace_orders").update({
            "provider": "clickpesa",
        }).eq("id", order_id).eq("status", "pending").eq("provider", "test").select("id").execute()
        claimed_rows = getattr(claim_result, "data", None) or []
        if not claimed_rows:
            latest_result = service.table("marketplace_orders").select(
                "provider,provider_checkout_url,status"
            ).eq("id", order_id).limit(1).execute()
            latest_rows = getattr(latest_result, "data", None) or []
            latest = latest_rows[0] if latest_rows else {}
            latest_url = latest.get("provider_checkout_url")
            if latest.get("provider") == "clickpesa" and isinstance(latest_url, str) and latest_url.startswith("https://"):
                return {
                    "success": True, "checkout": {**data, "provider": "clickpesa"},
                    "payment_status": "pending", "checkout_url": latest_url,
                    "live_payment_enabled": True,
                    "message": "Existing hosted checkout reused after concurrent initialization.",
                }
            raise HTTPException(status_code=409, detail="Checkout initialization is already in progress or requires manual review.")

        provider = ClickPesaClient()
        callback_url = os.getenv("CLICKPESA_CALLBACK_URL", "").strip() or None
        checkout_url = provider.generate_checkout_link(
            amount_tzs=int(order["total_amount"]),
            order_reference=order_id,
            description=title,
            customer_name=body.customer_name,
            customer_email=str(body.customer_email),
            customer_phone=body.customer_phone,
            callback_url=callback_url,
        )
        save_result = service.table("marketplace_orders").update({
            "provider_checkout_url": checkout_url,
        }).eq("id", order_id).eq("status", "pending").eq("provider", "clickpesa").is_("provider_checkout_url", "null").select("id").execute()
        saved_rows = getattr(save_result, "data", None) or []
        if not saved_rows:
            raise HTTPException(status_code=502, detail="Hosted checkout was created but could not be safely attached to the order; manual review is required.")
        return {
            "success": True,
            "checkout": {**data, "provider": "clickpesa"},
            "payment_status": "pending",
            "checkout_url": checkout_url,
            "live_payment_enabled": True,
            "message": "Hosted checkout created. The order remains pending until provider verification succeeds.",
        }
    except HTTPException:
        raise
    except (ClickPesaConfigurationError, ClickPesaAPIError, KeyError, TypeError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="ClickPesa checkout could not be created.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Checkout could not be safely finalized.") from exc



@router.post("/orders/{order_id}/reconcile")
def reconcile_marketplace_order(
    order_id: UUID,
    buyer_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Query ClickPesa and finalize only a unique, exact successful payment."""
    service = get_supabase_service_client()
    if service is None:
        raise HTTPException(status_code=503, detail="Payment reconciliation is not configured.")
    try:
        order_result = service.table("marketplace_orders").select(
            "id,buyer_id,status,total_amount,currency,provider,expires_at"
        ).eq("id", str(order_id)).limit(1).execute()
        orders = getattr(order_result, "data", None) or []
        order = orders[0] if orders else None
        if not order or order.get("buyer_id") != buyer_id:
            raise HTTPException(status_code=404, detail="Order not found.")
        if order.get("status") == "paid":
            return {"success": True, "payment_status": "paid", "already_processed": True}
        if order.get("status") != "pending" or order.get("provider") != "clickpesa":
            raise HTTPException(status_code=409, detail="Order is not eligible for ClickPesa reconciliation.")

        provider = ClickPesaClient()
        payments = provider.query_payment(str(order_id))
        verified = select_verified_success(
            payments,
            order_reference=str(order_id),
            expected_amount=int(order["total_amount"]),
            expected_currency=str(order["currency"]),
        )
        if verified is None:
            return {
                "success": True,
                "payment_status": "pending",
                "order_status_changed": False,
                "entitlement_granted": False,
            }

        event_row = {
            "order_id": str(order_id),
            "provider": "clickpesa",
            "provider_event_id": verified["provider_payment_reference"],
            "event_type": "PAYMENT RECEIVED",
            "verification_status": "received",
            "amount": verified["amount"],
            "currency": verified["currency"],
            "payload_sha256": hashlib.sha256(
                json.dumps(verified, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
        }
        # The provider query is authoritative. Refresh a webhook-first inbox row
        # (which may have omitted amount/currency) with the verified values.
        service.table("payment_events").upsert(
            event_row,
            on_conflict="provider,provider_event_id",
            ignore_duplicates=False,
        ).execute()
        confirmation = service.rpc(
            "confirm_marketplace_payment",
            {
                "p_order_id": str(order_id),
                "p_provider_event_id": verified["provider_payment_reference"],
                "p_amount": verified["amount"],
                "p_currency": verified["currency"],
            },
        ).execute()
        result = getattr(confirmation, "data", None)
        if isinstance(result, list):
            result = result[0] if result else None
        if not isinstance(result, dict) or result.get("confirmed") is not True:
            raise HTTPException(status_code=502, detail="Payment confirmation was not completed.")
        return {
            "success": True,
            "payment_status": "paid",
            "order_status_changed": not bool(result.get("already_processed")),
            "entitlements_granted": int(result.get("entitlements_granted", 0)),
        }
    except HTTPException:
        raise
    except (ClickPesaConfigurationError, ClickPesaAPIError) as exc:
        raise HTTPException(status_code=502, detail="ClickPesa payment status could not be checked.") from exc
    except PaymentReconciliationError as exc:
        raise HTTPException(status_code=409, detail="Payment details require manual verification.") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Payment reconciliation could not be completed.") from exc

@router.post("/webhooks/clickpesa", status_code=202)
async def receive_clickpesa_webhook(request: Request) -> dict[str, Any]:
    """Verify and inbox ClickPesa events; webhook alone never grants access."""
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

    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Invalid ClickPesa event data.")
    status_value = str(data.get("status", "")).strip().upper()
    event_type = payload.get("event") or payload.get("eventType")
    if event_type not in {"PAYMENT RECEIVED", "PAYMENT FAILED"}:
        # Some ClickPesa callback configurations send status in data rather than event.
        event_type = "PAYMENT RECEIVED" if status_value == "SUCCESS" else (
            "PAYMENT FAILED" if status_value in {"FAILED", "CANCELED"} else None
        )
    if event_type not in {"PAYMENT RECEIVED", "PAYMENT FAILED"}:
        raise HTTPException(status_code=400, detail="Unsupported ClickPesa event.")

    try:
        order_id = UUID(str(data.get("orderReference", "")))
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid order reference.") from exc

    provider_event_id = data.get("paymentReference") or data.get("paymentId") or data.get("id")
    if not isinstance(provider_event_id, str) or not provider_event_id.strip():
        raise HTTPException(status_code=400, detail="Missing provider payment reference.")

    amount = None
    amount_raw = data.get("collectedAmount")
    if amount_raw is not None:
        try:
            parsed = Decimal(str(amount_raw))
            if not parsed.is_finite() or parsed < 0 or parsed != parsed.to_integral_value():
                raise ValueError
            amount = int(parsed)
        except (InvalidOperation, ValueError):
            raise HTTPException(status_code=400, detail="Invalid collected amount.")
    currency = data.get("collectedCurrency")
    if currency is not None and not isinstance(currency, str):
        raise HTTPException(status_code=400, detail="Invalid collected currency.")

    service = get_supabase_service_client()
    if service is None:
        raise HTTPException(status_code=503, detail="Payment event storage is not configured.")
    row = {
        "order_id": str(order_id), "provider": "clickpesa",
        "provider_event_id": provider_event_id.strip(), "event_type": event_type,
        "verification_status": "received", "amount": amount, "currency": currency,
        "payload_sha256": hashlib.sha256(raw).hexdigest(),
    }
    try:
        service.table("payment_events").upsert(
            row, on_conflict="provider,provider_event_id", ignore_duplicates=True
        ).execute()
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not persist payment event.") from exc

    return {
        "accepted": True, "event_recorded": True, "event_type": event_type,
        "payment_reference": provider_event_id.strip(),
        "payment_status": "pending_verification",
        "order_status_changed": False, "entitlement_granted": False,
    }


__all__ = ["router"]


# Marketplace files are private objects; only this endpoint issues buyer download URLs.
_MARKETPLACE_FILE_BUCKET = "marketplace-products"
_MARKETPLACE_FILE_MAX_BYTES = 100 * 1024 * 1024
_MARKETPLACE_FILE_TYPES = {
    "application/pdf": ".pdf",
    "video/mp4": ".mp4",
    "application/epub+zip": ".epub",
}


@router.put("/products/{product_id}/file")
async def upload_marketplace_product_file(
    product_id: UUID,
    request: Request,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Upload/replace a creator's private product file; raw body, MIME allowlisted."""
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped storage access is not configured.")
    content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in _MARKETPLACE_FILE_TYPES:
        raise HTTPException(status_code=415, detail="Only PDF, MP4, and EPUB product files are supported.")
    try:
        declared_length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid content length.")
    if declared_length < 1 or declared_length > _MARKETPLACE_FILE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Product file must be between 1 byte and 100 MB.")
    body = await request.body()
    if len(body) != declared_length or len(body) > _MARKETPLACE_FILE_MAX_BYTES:
        raise HTTPException(status_code=413, detail="Product file size does not match the declared size or exceeds 100 MB.")
    # Reject obvious MIME spoofing before storing user-supplied bytes.
    if content_type == "application/pdf" and not body.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid PDF signature.")
    if content_type == "video/mp4" and (len(body) < 12 or body[4:8] != b"ftyp"):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid MP4 signature.")
    if content_type == "application/epub+zip" and not body.startswith(b"PK\\x03\\x04"):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid EPUB archive.")
    products = getattr(client.table("creator_products").select("id,status").eq(
        "id", str(product_id)
    ).eq("owner_id", owner_id).limit(1).execute(), "data", None) or []
    if not products:
        raise HTTPException(status_code=404, detail="Product listing not found.")
    if products[0].get("status") == "archived":
        raise HTTPException(status_code=409, detail="Archived products cannot receive file uploads.")
    from uuid import uuid4
    suffix = _MARKETPLACE_FILE_TYPES[content_type]
    storage_path = f"{owner_id}/{product_id}/{uuid4().hex}{suffix}"
    raw_name = request.headers.get("x-file-name", "product" + suffix)
    safe_name = "".join(ch for ch in raw_name.replace("\\", "/").split("/")[-1] if ch.isprintable())
    safe_name = safe_name.strip()[:180] or ("product" + suffix)
    try:
        client.storage.from_(_MARKETPLACE_FILE_BUCKET).upload(
            storage_path, body,
            file_options={"content-type": content_type, "upsert": "false"},
        )
        saved = client.table("marketplace_product_files").upsert({
            "product_id": str(product_id),
            "owner_id": owner_id,
            "storage_path": storage_path,
            "content_type": content_type,
            "file_size_bytes": len(body),
            "file_name": safe_name,
        }, on_conflict="product_id").select("product_id,content_type,file_size_bytes,file_name").execute()
        rows = getattr(saved, "data", None) or []
        if not rows:
            raise RuntimeError("Product file metadata was not confirmed.")
    except Exception as exc:
        # A failed metadata write leaves an inaccessible private object, never a public file.
        raise HTTPException(status_code=502, detail="Product file could not be safely attached to the listing.") from exc
    return {"success": True, "file": rows[0], "delivery_enabled": True}


@router.get("/orders/{order_id}/download")
def download_marketplace_product(
    order_id: UUID,
    buyer_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Issue a short-lived URL only when the caller owns an active paid entitlement."""
    service = get_supabase_service_client()
    if service is None:
        raise HTTPException(status_code=503, detail="Marketplace delivery is not configured.")
    try:
        entitlements = getattr(service.table("product_entitlements").select(
            "id,product_id,status"
        ).eq("order_id", str(order_id)).eq("buyer_id", buyer_id).eq(
            "status", "active"
        ).limit(1).execute(), "data", None) or []
        if not entitlements:
            raise HTTPException(status_code=404, detail="Paid product entitlement not found.")
        files = getattr(service.table("marketplace_product_files").select(
            "storage_path,content_type,file_name"
        ).eq("product_id", entitlements[0]["product_id"]).limit(1).execute(), "data", None) or []
        if not files:
            raise HTTPException(status_code=404, detail="Product file is not available yet.")
        signed = service.storage.from_(_MARKETPLACE_FILE_BUCKET).create_signed_url(
            files[0]["storage_path"], 60
        )
        url = signed.get("signedURL") or signed.get("signedUrl")
        if not isinstance(url, str) or not url.startswith("https://"):
            raise RuntimeError("Storage did not return a valid signed URL.")
        return {
            "success": True,
            "download_url": url,
            "file_name": files[0]["file_name"],
            "content_type": files[0]["content_type"],
            "expires_in_seconds": 60,
        }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=502, detail="A secure product download link could not be created.") from exc
