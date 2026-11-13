"""Meta webhook ingestion for HAPE BROTHERS marketing."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import PlainTextResponse

from app.database import get_supabase_service_client
from app.services.hape_marketing_os import HapeBrothersMarketingOS

router = APIRouter(prefix="/webhooks/meta", tags=["meta-webhooks"])


def _verify_signature(raw_body: bytes, signature: str | None) -> bool:
    secret = os.getenv("META_APP_SECRET", "")
    if not secret or not signature or not signature.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(signature[7:], expected)


def _is_meta_dashboard_sample(payload: dict[str, Any]) -> bool:
    """Recognize Meta's documented dashboard sample, never real customer traffic."""
    sample_messages = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            if (
                str(metadata.get("phone_number_id") or "") != "123456123"
                or str(metadata.get("display_phone_number") or "") != "16505551111"
            ):
                return False
            messages = value.get("messages") or []
            if not messages:
                return False
            for message in messages:
                if (
                    str(message.get("from") or "") != "16315551181"
                    or not str(message.get("id") or "").startswith("ABGG")
                ):
                    return False
                sample_messages += 1
    return sample_messages > 0


def _resolve_owner_id(phone_number_id: str | None) -> str | None:
    """Resolve a configured tenant UUID; never cross-fallback between Meta assets."""
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
        if not owner:
            return None
    else:
        owner = os.getenv("HAPE_BROTHERS_OWNER_ID", "").strip()
        if not owner:
            return None

    try:
        return str(UUID(str(owner).strip()))
    except (ValueError, AttributeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Meta webhook owner is not configured with a valid UUID",
        ) from exc


@router.get("", response_class=PlainTextResponse)
async def verify_meta_webhook(
    hub_mode: str | None = Query(default=None, alias="hub.mode"),
    hub_verify_token: str | None = Query(default=None, alias="hub.verify_token"),
    hub_challenge: str | None = Query(default=None, alias="hub.challenge"),
) -> PlainTextResponse:
    """Handle Meta's GET subscription verification with the exact dotted aliases."""
    verify_token = os.getenv("META_WEBHOOK_VERIFY_TOKEN", "")
    if (
        hub_mode == "subscribe"
        and verify_token
        and hmac.compare_digest(hub_verify_token or "", verify_token)
        and hub_challenge is not None
    ):
        return PlainTextResponse(content=hub_challenge, status_code=status.HTTP_200_OK)
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail="Webhook verification failed",
    )


@router.post("")
async def receive_meta_webhook(request: Request) -> dict[str, Any]:
    """Verify and persist text messages as tenant-owned HAPE BROTHERS leads."""
    raw = await request.body()
    if not _verify_signature(raw, request.headers.get("X-Hub-Signature-256")):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Meta webhook signature",
        )
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid webhook JSON") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="Invalid webhook payload")

    # Meta's dashboard "Send to server" fixture uses documented placeholder
    # assets and sender IDs. Acknowledge that signed sample without creating a
    # customer lead or requiring a tenant mapping. Real, unmapped assets still
    # fail closed below.
    if _is_meta_dashboard_sample(payload):
        sample_count = sum(
            len(change.get("value", {}).get("messages") or [])
            for entry in payload.get("entry", [])
            for change in entry.get("changes", [])
            if change.get("field") == "messages"
        )
        return {"success": True, "processed": 0, "ignored": sample_count, "leads": []}

    client = get_supabase_service_client()
    if client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Server-side Supabase service credentials are not configured",
        )

    processed = 0
    ignored = 0
    leads: list[dict[str, Any]] = []

    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            metadata = value.get("metadata") or {}
            phone_number_id = metadata.get("phone_number_id")
            owner_id = _resolve_owner_id(str(phone_number_id) if phone_number_id else None)
            messages = value.get("messages", [])
            if messages and not owner_id:
                # Fail visibly instead of acknowledging and silently dropping a
                # message that cannot be assigned to the correct tenant.
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="No owner is configured for this Meta phone number",
                )

            for message in messages:
                if message.get("type") != "text":
                    ignored += 1
                    continue
                sender = message.get("from")
                body = ((message.get("text") or {}).get("body") or "").strip()
                message_id = message.get("id")
                if not sender or not body or not message_id or not owner_id:
                    ignored += 1
                    continue

                service = HapeBrothersMarketingOS(owner_id=owner_id, client=client)
                result = service.create_lead(
                    name=f"WhatsApp {sender}",
                    phone=sender,
                    source="whatsapp",
                    channel="whatsapp",
                    need=body,
                    idempotency_key=f"whatsapp:{phone_number_id}:{message_id}",
                )
                if not result.get("success"):
                    # Do not acknowledge an event as processed if lead persistence failed.
                    raise HTTPException(
                        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                        detail="Could not persist inbound WhatsApp lead",
                    )

                mission = result.get("mission") or {}
                lead_id = mission.get("id")
                qualification_result = (
                    service.qualify_lead(lead_id, message=body)
                    if lead_id
                    else {"success": False, "error": "Created lead has no mission ID"}
                )
                if not qualification_result.get("success"):
                    # The lead is durable, but do not claim qualification or approval
                    # is complete when its metadata update failed.
                    qualification_payload = None
                    follow_up_status = "qualification_failed"
                else:
                    updated_mission = qualification_result.get("mission") or {}
                    mission_result = updated_mission.get("result") or {}
                    qualification_payload = (
                        mission_result.get("qualification")
                        or qualification_result.get("qualification")
                    )
                    follow_up = service.suggest_follow_up(lead_id)
                    if follow_up.get("success"):
                        follow_up_status = follow_up.get("status", "waiting_approval")
                        follow_up_payload = {
                            "message": follow_up.get("message"),
                            "approval_request_id": follow_up.get("approval_request_id"),
                        }
                    else:
                        follow_up_status = "approval_failed"
                        follow_up_payload = {"error": follow_up.get("error", "Follow-up approval could not be created")}
                processed += 1
                leads.append(
                    {
                        "lead_id": lead_id,
                        "phone": sender,
                        "message_id": message_id,
                        "phone_number_id": phone_number_id,
                        "qualification": qualification_payload,
                        "follow_up_status": follow_up_status,
                        "follow_up": follow_up_payload if qualification_result.get("success") else None,
                    }
                )

    return {"success": True, "processed": processed, "ignored": ignored, "leads": leads}
