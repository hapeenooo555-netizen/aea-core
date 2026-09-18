"""Affiliate job API routes for P1-10."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.mission_orchestration import MissionOrchestrationService
from app.services.stores.pin_publish_store import PinPublishStore

router = APIRouter(prefix="/affiliate", tags=["affiliate"])

SUPPORTED_PLATFORMS = {"pinterest"}

MAX_TITLE_LENGTH = 200


class AffiliateJobCreateRequest(BaseModel):
    """Request payload for creating an affiliate job."""

    platform: str = Field(..., description="Platform for the affiliate job (e.g. 'pinterest').")
    country: str = Field(..., min_length=1, max_length=10)
    language: str = Field(..., min_length=1, max_length=10)
    niche: str = Field(..., min_length=1)
    daily_limit: int = Field(..., ge=1)
    human_approval_required: bool = True
    title: str | None = None
    priority: str = "normal"
    urgency: str = "normal"
    business_importance: int = Field(default=1, ge=1, le=5)
    scheduled_at: datetime | None = None
    recurrence: str | None = None
    idempotency_key: str | None = None
    run_now: bool = False

    model_config = ConfigDict(extra="forbid")


class AffiliateJobResponse(BaseModel):
    """Product-friendly response for an affiliate job."""

    id: str
    objective: str
    title: str | None = None
    status: str
    raw_status: str | None = None
    lifecycle_status: str | None = None
    priority: str
    urgency: str
    business_importance: int
    scheduled_at: str | None = None
    recurrence: str | None = None
    next_retry_at: str | None = None
    attempt_count: int
    max_attempts: int
    platform: str | None = None
    country: str | None = None
    language: str | None = None
    niche: str | None = None
    daily_limit: int | None = None
    human_approval_required: bool | None = None
    approval_request_id: str | None = None
    operation_key: str | None = None
    pin_id: str | None = None
    publish_link_url: str | None = None
    published_at: str | None = None
    duplicate_safe: bool | None = None
    created_at: str
    updated_at: str

    model_config = ConfigDict(extra="allow")


class AffiliateJobListResponse(BaseModel):
    """Paginated list of affiliate jobs."""

    success: bool = True
    jobs: list[AffiliateJobResponse]
    count: int

    model_config = ConfigDict(extra="allow")


def _extract_publish_field(result: dict[str, Any], field_name: str) -> Any:
    """Extract a durable publish field from either the top-level result or the nested result payload."""
    if not isinstance(result, dict):
        return None
    value = result.get(field_name)
    if value is not None:
        return value
    nested = result.get("result")
    if isinstance(nested, dict):
        value = nested.get(field_name)
        if value is not None:
            return value
    return None


def _product_lifecycle_status(
    raw_status: str | None,
    result: dict[str, Any] | None,
    *,
    owner_id: str | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Map raw mission state to the user-visible lifecycle.

    A mission is only reported as published when there is a durable row in the
    authenticated user's ``public.published_pins`` table for the derived
    ``operation_key``. This prevents transient ``mission.result`` payloads from
    being mistaken for final published state.
    """
    raw = str(raw_status or "pending").lower()
    result = result or {}
    if isinstance(result, str):
        result = {}

    note = str((result.get("note") or _extract_publish_field(result, "note") or "")).lower()
    nested_result = result.get("result") if isinstance(result.get("result"), dict) else {}
    operation_key = _extract_publish_field(result, "operation_key")
    durable_publish = None
    if owner_id and operation_key:
        durable_publish = PinPublishStore(client=client).get_by_operation_key(owner_id, str(operation_key))

    if raw in {"pending", "scheduled"}:
        lifecycle = "created"
    elif raw == "waiting_approval":
        lifecycle = "approval_pending"
    elif raw == "waiting_human":
        lifecycle = "human_action_required"
    elif raw == "active":
        lifecycle = "running"
    elif raw == "retrying":
        lifecycle = "retryable"
    elif raw == "failed":
        lifecycle = "failed"
    elif raw == "cancelled":
        lifecycle = "cancelled"
    elif raw == "paused":
        lifecycle = "paused"
    elif raw == "completed":
        if durable_publish is not None:
            lifecycle = "published"
        else:
            lifecycle = "completed"
    else:
        lifecycle = raw or "created"

    return {
        "raw_status": raw,
        "lifecycle_status": lifecycle,
        "duplicate_safe": note == "pin_already_published" or result.get("status") == "duplicate" or nested_result.get("status") == "duplicate",
    }


def _normalize_job(row: dict[str, Any], *, owner_id: str | None = None, client: Any | None = None) -> dict[str, Any]:
    """Normalize a mission row into a product-friendly affiliate job dict."""
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        from json import loads as _json_loads
        metadata = _json_loads(metadata)
    result = row.get("result") or {}
    if isinstance(result, str):
        from json import loads as _json_loads
        try:
            result = _json_loads(result)
        except Exception:
            result = {}

    operation_key = _extract_publish_field(result, "operation_key")
    durable_publish = None
    if owner_id and operation_key:
        durable_publish = PinPublishStore(client=client).get_by_operation_key(owner_id, str(operation_key))

    lifecycle = _product_lifecycle_status(row.get("status"), result, owner_id=owner_id, client=client)
    approval_request_id = durable_publish.get("approval_request_id") if durable_publish else _extract_publish_field(result, "approval_request_id")
    pin_id = durable_publish.get("pin_id") if durable_publish else _extract_publish_field(result, "pin_id")
    publish_link_url = durable_publish.get("link_url") if durable_publish else _extract_publish_field(result, "link_url")
    published_at = durable_publish.get("created_at") if durable_publish else _extract_publish_field(result, "published_at")

    if durable_publish and durable_publish.get("status") == "published":
        lifecycle["lifecycle_status"] = "published"
        lifecycle["raw_status"] = "completed"

    return {
        "id": row.get("id"),
        "objective": row.get("objective") or row.get("description") or row.get("title") or "",
        "title": row.get("title"),
        "status": lifecycle["lifecycle_status"],
        "raw_status": lifecycle["raw_status"],
        "lifecycle_status": lifecycle["lifecycle_status"],
        "priority": row.get("priority", "normal"),
        "urgency": row.get("urgency", "normal"),
        "business_importance": row.get("business_importance", 1),
        "scheduled_at": row.get("scheduled_at"),
        "recurrence": row.get("recurrence"),
        "next_retry_at": row.get("next_retry_at"),
        "attempt_count": row.get("attempt_count", 0),
        "max_attempts": row.get("max_attempts", 3),
        "platform": metadata.get("platform") if isinstance(metadata, dict) else None,
        "country": metadata.get("country") if isinstance(metadata, dict) else None,
        "language": metadata.get("language") if isinstance(metadata, dict) else None,
        "niche": metadata.get("niche") if isinstance(metadata, dict) else None,
        "daily_limit": metadata.get("daily_limit") if isinstance(metadata, dict) else None,
        "human_approval_required": metadata.get("human_approval_required") if isinstance(metadata, dict) else None,
        "approval_request_id": approval_request_id,
        "operation_key": operation_key,
        "pin_id": pin_id,
        "publish_link_url": publish_link_url,
        "published_at": published_at,
        "duplicate_safe": lifecycle["duplicate_safe"],
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


def _build_objective(body: AffiliateJobCreateRequest) -> str:
    """Build the objective string from affiliate job fields."""
    parts = [
        f"Start affiliate marketing on {body.platform}",
        f"Country: {body.country}",
        f"Language: {body.language}",
        f"Niche: {body.niche}",
        f"Daily limit: {body.daily_limit} pins",
    ]
    return " | ".join(parts)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_affiliate_job(
    body: AffiliateJobCreateRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Create a dedicated affiliate job for the authenticated user.

    Only platform ``"pinterest"`` is supported in P1-10.
    """

    platform = body.platform.strip().lower()
    if platform not in SUPPORTED_PLATFORMS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Unsupported platform '{body.platform}'. Supported: {sorted(SUPPORTED_PLATFORMS)}",
        )

    service = MissionOrchestrationService(current_user_id, client=client)

    affiliate_metadata: dict[str, Any] = {
        "platform": platform,
        "country": body.country,
        "language": body.language,
        "niche": body.niche,
        "daily_limit": body.daily_limit,
        "human_approval_required": body.human_approval_required,
        "vertical": "affiliate",
        "content_inputs": {
            "platform": platform,
            "vertical": "affiliate",
            "country": body.country,
            "language": body.language,
            "niche": body.niche,
            "daily_limit": body.daily_limit,
            "human_approval_required": body.human_approval_required,
        },
    }

    objective = _build_objective(body)
    title = body.title or f"Affiliate: {body.platform} ({body.niche})"

    result = service.create_mission(
        objective,
        title=title[:MAX_TITLE_LENGTH],
        priority=body.priority.lower(),
        urgency=body.urgency.lower(),
        business_importance=body.business_importance,
        scheduled_at=body.scheduled_at.isoformat() if body.scheduled_at else None,
        recurrence=body.recurrence,
        dependencies=[],
        metadata=affiliate_metadata,
        idempotency_key=body.idempotency_key,
    )

    if not result.get("success"):
        error = result.get("error", "Failed to create affiliate job")
        code = status.HTTP_404_NOT_FOUND if "not found" in error.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=code, detail=error)

    mission = result.get("mission") or {}
    normalized = _normalize_job(mission, owner_id=current_user_id, client=client)

    response: dict[str, Any] = {
        "success": True,
        "job": normalized,
        "mission_id": normalized["id"],
    }

    if body.run_now:
        run_result = service.run_mission(normalized["id"])
        response["run"] = run_result
        if run_result.get("success"):
            mission = service.get_mission(normalized["id"]) or {}
            response["job"] = _normalize_job(mission, owner_id=current_user_id, client=client)

    if result.get("idempotent"):
        response["idempotent"] = True

    return response


@router.get("")
async def list_affiliate_jobs(
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
    status_filter: str | None = Query(default=None, alias="status"),
) -> AffiliateJobListResponse:
    """List affiliate jobs owned by the authenticated user."""

    service = MissionOrchestrationService(current_user_id, client=client)
    missions = service.list_missions(status=status_filter, limit=100)

    affiliate_jobs: list[dict[str, Any]] = []
    for m in missions:
        metadata = m.get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        if metadata.get("vertical") == "affiliate" or metadata.get("platform") == "pinterest":
            affiliate_jobs.append(_normalize_job(m, owner_id=current_user_id, client=client))

    return AffiliateJobListResponse(
        success=True,
        jobs=[AffiliateJobResponse(**j) for j in affiliate_jobs],
        count=len(affiliate_jobs),
    )


@router.get("/{mission_id}")
async def get_affiliate_job(
    mission_id: str,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Retrieve a single affiliate job owned by the authenticated user."""

    service = MissionOrchestrationService(current_user_id, client=client)
    mission = service.get_mission(mission_id)
    if mission is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Affiliate job not found")

    normalized = _normalize_job(mission, owner_id=current_user_id, client=client)
    return {"success": True, "job": normalized}
