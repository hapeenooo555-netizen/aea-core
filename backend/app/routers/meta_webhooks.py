"""Meta webhook ingestion for HAPE BROTHERS marketing."""
from __future__ import annotations

import hashlib
import hmac
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status

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


@router.get("")
async def verify_meta_webhook(
    hub_mode: str | None = None,
    hub_verify_token: str | None = None,
    hub_challenge: str | None = None,
) -> Any:
    """Handle Meta webhook subscription verification."""
    verify_token = os.getenv("META_WEBHOOK_VERIFY_TOKEN")
    if (
        hub_mode == "subscribe"
        and verify_token
        and hmac.compare_digest(hub_verify_token or "", verify_token)
        and hub_challenge
    ):
        return int(hub_challenge)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webhook verification failed")


@router.post("")
async def receive_meta_webhook(request: Request) -> dict[str, Any]:
    """Verify and ingest WhatsApp Cloud API inbound messages."""
    raw = await request.body()
    if not _verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Meta webhook signature")

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
                service = HapeBrothersMarketingOS(
                    owner_id=os.getenv("HAPE_BROTHERS_OWNER_ID", "meta-webhook"),
                    client=None,
                )
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
                if result.get("success"):
                    lead_id = result.get("mission", {}).get("id")
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
                else:
                    ignored += 1

    return {
        "success": True,
        "processed": processed,
        "ignored": ignored,
        "leads": leads,
    }
