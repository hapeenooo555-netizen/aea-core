"""Meta webhook ingestion for HAPE BROTHERS marketing."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.database import get_supabase_client
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


def _resolve_owner_id(phone_number_id: str | None) -> str | None:
    """Resolve the tenant from the Meta asset ID; never cross-fallback a configured map."""
    raw_map = os.getenv("META_PHONE_NUMBER_OWNER_MAP", "").strip()
    if raw_map:
        try:
            owner_map = json.loads(raw_map)
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Meta phone-number owner mapping is invalid",
            ) from exc
        if not isinstance(owner_map, dict):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Meta phone-number owner mapping must be a JSON object",
            )
        owner = owner_map.get(str(phone_number_id or ""))
        return str(owner).strip() if owner and str(owner).strip() else None

    # Backward-compatible single-tenant configuration. Once a mapping is supplied,
    # an unknown asset must not fall back to this owner.
    owner = os.getenv("HAPE_BROTHERS_OWNER_ID", "").strip()
    return owner or None


@router.get("")
async def verify_meta_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> Any:
    """Handle Meta webhook subscription verification."""
    verify_token = os.getenv("META_WEBHOOK_VERIFY_TOKEN")
    if (
        hub_mode == "subscribe"
        and verify_token
        and hmac.compare_digest(hub_verify_token or "", verify_token)
        and hub_challenge
    ):
        return PlainTextResponse(hub_challenge)
    raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Webhook verification failed")


@router.post("")
async def receive_meta_webhook(request: Request) -> dict[str, Any]:
    """Verify and ingest WhatsApp Cloud API inbound messages."""
    raw = await request.body()
    if not _verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Meta webhook signature")

    payload = await request.json()
    # Validate the configuration before processing any event. A supplied mapping is
    # authoritative; unknown phone_number_id values are ignored, not assigned elsewhere.
    _resolve_owner_id(None)
    if not os.getenv("META_PHONE_NUMBER_OWNER_MAP", "").strip() and not os.getenv("HAPE_BROTHERS_OWNER_ID", "").strip():
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Meta webhook owner is not configured")

    client = get_supabase_client()
    if client is None:
        # Webhooks must never report success after writing leads only to process memory.
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Persistent lead storage is not configured")

    processed = 0
    ignored = 0
    leads: list[dict[str, Any]] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value") or {}
            if change.get("field") != "messages":
                continue
            metadata = value.get("metadata") or {}
            phone_number_id = metadata.get("phone_number_id")
            owner_id = _resolve_owner_id(str(phone_number_id) if phone_number_id else None)
            for message in value.get("messages", []):
                if message.get("type") != "text":
                    ignored += 1
                    continue
                sender = message.get("from")
                text = ((message.get("text") or {}).get("body") or "").strip()
                message_id = message.get("id")
                if not sender or not text or not owner_id:
                    ignored += 1
                    continue

                service = HapeBrothersMarketingOS(owner_id=owner_id, client=client)
                result = service.create_lead(
                    name=f"WhatsApp {sender}",
                    phone=sender,
                    company=None,
                    location=None,
                    source="whatsapp",
                    channel="whatsapp",
                    need=text,
                    idempotency_key=f"whatsapp:{phone_number_id}:{message_id}" if message_id else None,
                )
                if result.get("success"):
                    lead_id = result.get("mission", {}).get("id")
                    qualification = service.qualify_lead(lead_id, message=text) if lead_id else {"success": False}
                    processed += 1
                    leads.append({
                        "lead_id": lead_id,
                        "phone": sender,
                        "message_id": message_id,
                        "phone_number_id": phone_number_id,
                        "qualification": qualification.get("qualification") if qualification.get("success") else None,
                        "follow_up_status": "waiting_approval",
                    })
                else:
                    # Do not acknowledge a message that failed to persist; Meta can retry it.
                    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="WhatsApp lead persistence failed")

    return {
        "success": True,
        "processed": processed,
        "ignored": ignored,
        "leads": leads,
    }
