"""Affiliate job API routes for P1-10."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.mission_orchestration import MissionOrchestrationService

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
    created_at: str
    updated_at: str

    model_config = ConfigDict(extra="allow")


class AffiliateJobListResponse(BaseModel):
    """Paginated list of affiliate jobs."""

    success: bool = True
    jobs: list[AffiliateJobResponse]
    count: int

    model_config = ConfigDict(extra="allow")


def _normalize_job(row: dict[str, Any]) -> dict[str, Any]:
    """Normalize a mission row into a product-friendly affiliate job dict."""
    metadata = row.get("metadata") or {}
    if isinstance(metadata, str):
        from json import loads as _json_loads
        metadata = _json_loads(metadata)
    return {
        "id": row.get("id"),
        "objective": row.get("objective") or row.get("description") or row.get("title") or "",
        "title": row.get("title"),
        "status": row.get("status"),
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
    normalized = _normalize_job(mission)

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
            response["job"] = _normalize_job(mission)

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
            affiliate_jobs.append(_normalize_job(m))

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

    normalized = _normalize_job(mission)
    return {"success": True, "job": normalized}
