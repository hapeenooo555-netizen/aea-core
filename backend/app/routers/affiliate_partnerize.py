"""Authenticated Partnerize campaign discovery and opt-in application routes."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id
from app.services.partnerize import PartnerizeClient, PartnerizeError

router = APIRouter(prefix="/affiliate/partnerize", tags=["affiliate-partnerize"])


class CampaignRequestItem(BaseModel):
    advertiser_id: str = Field(..., min_length=1, max_length=100)
    campaign_id: str = Field(..., min_length=1, max_length=100)

    model_config = ConfigDict(extra="forbid")


class CampaignRequestsBody(BaseModel):
    campaign_requests: list[CampaignRequestItem] = Field(..., min_length=1, max_length=20)

    model_config = ConfigDict(extra="forbid")


def _raise_partnerize(exc: PartnerizeError) -> HTTPException:
    message = str(exc)
    code = 503 if "not configured" in message.lower() else 502
    if "denied access" in message.lower():
        code = 403
    if "rejected the api credentials" in message.lower():
        code = 502
    return HTTPException(status_code=code, detail=message)


@router.get("/status")
async def partnerize_status(_owner_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """Return configuration status only; never reveal credential values."""
    client = PartnerizeClient()
    return {
        "configured": client.configured,
        "application_key_configured": bool(client.application_key),
        "api_key_configured": bool(client.api_key),
        "publisher_id_configured": bool(client.publisher_id),
        "credentials_exposed": False,
    }


@router.get("/campaigns")
async def discover_partnerize_campaigns(_owner_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """Fetch campaign discovery data from Partnerize; this is read-only."""
    try:
        return PartnerizeClient().discover_campaigns()
    except PartnerizeError as exc:
        raise _raise_partnerize(exc) from exc


@router.post("/campaign-requests")
async def request_partnerize_campaigns(
    body: CampaignRequestsBody,
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Submit explicit campaign IDs to Partnerize. This action sends real applications."""
    try:
        client = PartnerizeClient()
        requests = [item.model_dump() for item in body.campaign_requests]
        result = client.request_campaigns(requests)
        return {
            "success": True,
            "submitted_count": len(requests),
            "partnerize_response": result,
            "note": "Submission does not guarantee advertiser approval.",
        }
    except PartnerizeError as exc:
        raise _raise_partnerize(exc) from exc


__all__ = ["router"]
