"""HAPE BROTHERS Marketing OS API."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.hape_marketing_os import HapeBrothersMarketingOS
from app.services.connectors.meta_marketing import MetaMarketingConnector

router = APIRouter(prefix="/marketing", tags=["hape-brothers-marketing"])


class CampaignRequest(BaseModel):
    objective: str = Field(..., min_length=3, max_length=500)
    channels: list[str] = Field(..., min_length=1)
    audience: str = Field(..., min_length=2, max_length=500)
    call_to_action: str = Field(..., min_length=2, max_length=300)
    approval_required: bool = True
    idempotency_key: str | None = None

    model_config = ConfigDict(extra="forbid")


class ContentRequest(BaseModel):
    channel: str
    topic: str = Field(..., min_length=2, max_length=300)
    offer: str = Field(..., min_length=2, max_length=500)
    call_to_action: str = Field(..., min_length=2, max_length=300)
    language: str = Field(default="en", min_length=2, max_length=10)
    approval_required: bool = True
    idempotency_key: str | None = None

    model_config = ConfigDict(extra="forbid")


class LeadRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=200)
    phone: str | None = Field(default=None, max_length=40)
    company: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    source: str = Field(default="manual", max_length=50)
    channel: str = Field(default="whatsapp", max_length=30)
    need: str | None = Field(default=None, max_length=500)
    quantity: int | None = Field(default=None, ge=1)
    idempotency_key: str | None = None

    model_config = ConfigDict(extra="forbid")


class FollowUpRequest(BaseModel):
    message: str = Field(..., min_length=2, max_length=2000)
    channel: str = "whatsapp"
    next_action: str = "follow_up"

    model_config = ConfigDict(extra="forbid")


class PublishRequest(BaseModel):
    channel: str
    message: str = Field(..., min_length=1, max_length=5000)
    approval_required: bool = True
    idempotency_key: str | None = None

    model_config = ConfigDict(extra="forbid")


class DealRequest(BaseModel):
    stage: str
    amount: float | None = Field(default=None, ge=0)
    currency: str = "TZS"
    notes: str | None = Field(default=None, max_length=2000)

    model_config = ConfigDict(extra="forbid")


def _service(owner_id: str, client: Any) -> HapeBrothersMarketingOS:
    return HapeBrothersMarketingOS(owner_id, client=client)


def _result_or_raise(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("success"):
        return result
    error = str(result.get("error") or "Marketing operation failed")
    code = status.HTTP_404_NOT_FOUND if "not found" in error.lower() else status.HTTP_400_BAD_REQUEST
    raise HTTPException(status_code=code, detail=error)


@router.get("/integrations/meta/status")
async def meta_status(
    owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    return {"success": True, "owner_id": owner_id, "connector": MetaMarketingConnector().health_check()}


@router.post("/publish")
async def publish_marketing_content(
    body: PublishRequest,
    owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    if body.approval_required:
        return {
            "success": True,
            "status": "waiting_approval",
            "owner_id": owner_id,
            "action": "publish_content",
            "channel": body.channel.lower(),
            "message": body.message,
            "idempotency_key": body.idempotency_key,
        }
    return MetaMarketingConnector().publish_content(
        owner_id,
        {"channel": body.channel, "message": body.message},
        idempotency_key=body.idempotency_key,
    )


@router.get("/dashboard")
async def marketing_dashboard(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    return _service(owner_id, client).dashboard()


@router.post("/campaigns", status_code=status.HTTP_201_CREATED)
async def create_campaign(
    body: CampaignRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    return _result_or_raise(_service(owner_id, client).create_campaign(
        objective=body.objective,
        channels=body.channels,
        audience=body.audience,
        call_to_action=body.call_to_action,
        approval_required=body.approval_required,
        idempotency_key=body.idempotency_key,
    ))


@router.post("/content", status_code=status.HTTP_201_CREATED)
async def create_content(
    body: ContentRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    return _result_or_raise(_service(owner_id, client).create_content(
        channel=body.channel,
        topic=body.topic,
        offer=body.offer,
        call_to_action=body.call_to_action,
        language=body.language,
        approval_required=body.approval_required,
        idempotency_key=body.idempotency_key,
    ))


@router.post("/leads", status_code=status.HTTP_201_CREATED)
async def create_lead(
    body: LeadRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    return _result_or_raise(_service(owner_id, client).create_lead(
        name=body.name,
        phone=body.phone,
        company=body.company,
        location=body.location,
        source=body.source,
        channel=body.channel,
        need=body.need,
        quantity=body.quantity,
        idempotency_key=body.idempotency_key,
    ))


@router.get("/leads")
async def list_leads(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    leads = _service(owner_id, client).list_leads()
    return {"success": True, "leads": leads, "count": len(leads)}


@router.post("/leads/{lead_id}/follow-up")
async def follow_up_lead(
    lead_id: str,
    body: FollowUpRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    return _result_or_raise(_service(owner_id, client).follow_up_lead(
        lead_id,
        message=body.message,
        channel=body.channel,
        next_action=body.next_action,
    ))


@router.post("/leads/{lead_id}/deal")
async def update_deal(
    lead_id: str,
    body: DealRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    return _result_or_raise(_service(owner_id, client).record_deal(
        lead_id,
        stage=body.stage,
        amount=body.amount,
        currency=body.currency,
        notes=body.notes,
    ))


__all__ = ["router"]
