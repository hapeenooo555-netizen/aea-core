"""Meta webhook ingestion for HAPE BROTHERS marketing."""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app import database
from app.services.hape_marketing_os import HapeBrothersMarketingOS

router = APIRouter(prefix="/webhooks/meta", tags=["meta-webhooks"])


def _verify_signature(raw_body: bytes, signature: str | None) -> bool:
    secret = os.getenv("META_APP_SECRET")
    if not secret:
        return False
    if not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


@router.get("", response_class=PlainTextResponse)
async def verify_meta_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    """Handle Meta's GET subscription verification using its dotted query names."""
    verify_token = os.getenv("META_WEBHOOK_VERIFY_TOKEN")
    if (
        hub_mode == "subscribe"
        and verify_token
        and hmac.compare_digest(hub_verify_token or "", verify_token)
        and hub_challenge
    ):
        return PlainTextResponse(content=hub_challenge, status_code=status.HTTP_200_OK)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webhook verification failed")


@router.post("")
async def receive_meta_webhook(request: Request) -> dict[str, Any]:
    """Verify and persist WhatsApp Cloud API inbound messages."""
    raw = await request.body()
    if not _verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Meta webhook signature")

    # Do not accept a default owner or allow an in-memory fallback for inbound
    # business leads. The owner must be explicitly configured and persisted.
    configured_owner = os.getenv("HAPE_BROTHERS_OWNER_ID", "").strip()
    try:
        owner_id = str(UUID(configured_owner))
    except (ValueError, AttributeError):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook owner is not configured",
        )

    client = database.get_meta_webhook_client()
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook persistence is not configured",
        )

    payload = await request.json()
    processed = 0
    ignored = 0
    leads: list[dict[str, Any]] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            if change.get("field") != "messages":
                continue
            for message in value.get("messages", []):
                if message.get("type") != "text":
                    ignored += 1
                    continue
                sender = message.get("from")
                text = ((message.get("text") or {}).get("body") or "").strip()
                message_id = message.get("id")
                if not sender or not text:
                    ignored += 1
                    continue

                metadata = value.get("metadata") or {}
                service = HapeBrothersMarketingOS(owner_id=owner_id, client=client)
                result = service.create_lead(
                    name=f"WhatsApp {sender}",
                    phone=sender,
                    company=None,
                    location=None,
                    source="whatsapp",
                    channel="whatsapp",
                    need=text,
                    idempotency_key=f"whatsapp:{message_id}" if message_id else None,
                )
                if not result.get("success"):
                    # Return a retryable failure rather than acknowledging data
                    # that was not durably stored. Idempotency protects retries.
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Could not persist inbound WhatsApp lead",
                    )

                mission = result.get("mission") or {}
                lead_id = mission.get("id")
                qualification = service.qualify_lead(lead_id, message=text) if lead_id else {"success": False}
                processed += 1
                leads.append({
                    "lead_id": lead_id,
                    "phone": sender,
                    "message_id": message_id,
                    "phone_number_id": metadata.get("phone_number_id"),
                    "qualification": qualification.get("qualification") if qualification.get("success") else None,
                    "follow_up_status": "waiting_approval",
                })

    return {"success": True, "processed": processed, "ignored": ignored, "leads": leads}
