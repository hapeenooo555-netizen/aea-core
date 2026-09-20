"""Atlas router for AEA Core CEO AI mission orchestration."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from app import database as database_module
from app.dependencies import get_current_user_id, get_user_scoped_client, verify_mission_ownership
from app.services.mission_engine import MissionEngine

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/atlas",
    tags=["atlas"],
    dependencies=[Depends(get_current_user_id)],
)

VALID_COMMAND_TYPES = (
    "CREATE_MISSION",
    "START_RESEARCH",
    "START_CONTENT",
    "START_PUBLISH",
    "START_ANALYTICS",
    "PAUSE",
    "RESUME",
    "RETRY",
)


class MissionCreateRequest(BaseModel):
    """Request body for creating a new Atlas mission."""

    title: str
    description: str | None = None
    assigned_worker: str | None = None
    priority: str | None = None
    status: str | None = None
    progress: int | None = None
    result: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class MissionPatchRequest(BaseModel):
    """Request body for updating an existing Atlas mission."""

    status: str | None = None
    progress: int | None = None
    assigned_worker: str | None = None
    result: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class MissionCreateResponse(BaseModel):
    """Response payload returned after creating a mission."""

    id: str | int | None = None
    mission_id: str | int | None = None
    title: str | None = None
    description: str | None = None
    assigned_worker: str | None = None
    priority: str | None = None
    status: str | None = None
    progress: int | None = None
    result: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    steps: list[dict[str, Any]] | None = None
    first_command: dict[str, Any] | None = None

    model_config = ConfigDict(extra="allow")


class AtlasCommandCreateRequest(BaseModel):
    """Request body for issuing a new command to a worker."""

    command_type: Literal[VALID_COMMAND_TYPES]  # type: ignore[arg-type]
    target_worker: str | None = None
    mission_id: str | None = None
    payload: dict[str, Any] | None = None


class AtlasCommandResponse(BaseModel):
    """Response payload for a created Atlas command."""

    id: str | None = None
    command_type: str | None = None
    target_worker: str | None = None
    mission_id: str | None = None
    payload: dict[str, Any] | None = None
    status: str | None = None
    created_at: str | None = None

    model_config = ConfigDict(extra="allow")


class MissionStepResponse(BaseModel):
    """Representation of a mission step."""

    id: str | None = None
    mission_id: str | None = None
    step_name: str | None = None
    worker_role: str | None = None
    status: str | None = None
    created_at: str | None = None

    model_config = ConfigDict(extra="allow")


class MissionDetailResponse(BaseModel):
    """Mission payload with nested steps."""

    id: str | None = None
    title: str | None = None
    description: str | None = None
    assigned_worker: str | None = None
    priority: str | None = None
    status: str | None = None
    progress: int | None = None
    result: dict[str, Any] | None = None
    created_at: str | None = None
    updated_at: str | None = None
    steps: list[MissionStepResponse] | None = None

    model_config = ConfigDict(extra="allow")


class WorkerResponseRecord(BaseModel):
    """Worker response payload sent back to Atlas."""

    id: str | None = None
    response_type: str | None = None
    worker_id: str | None = None
    mission_id: str | None = None
    command_id: str | None = None
    payload: dict[str, Any] | None = None
    created_at: str | None = None

    model_config = ConfigDict(extra="allow")


def _format_supabase_error(exc: Exception) -> str:
    """Extract a readable error string from Supabase exceptions."""

    if hasattr(exc, "message") and exc.message:
        return str(exc.message)
    if hasattr(exc, "details") and exc.details:
        return str(exc.details)
    if hasattr(exc, "response"):
        response = getattr(exc, "response", None)
        if response is not None:
            text = getattr(response, "text", None) or getattr(response, "content", None)
            if text:
                return str(text)
    return str(exc)


def _build_mission_payload(request: MissionCreateRequest, owner_id: str | None = None) -> dict[str, Any]:
    """Build a mission payload compatible with the current Supabase schema."""

    payload: dict[str, Any] = {
        "title": request.title,
        "description": request.description,
        "assigned_worker": request.assigned_worker,
        "priority": request.priority or "normal",
        "status": request.status or "pending",
        "progress": request.progress if request.progress is not None else 0,
        "result": request.result or {},
        "retry_count": 0,
    }
    if owner_id:
        payload["owner_id"] = owner_id
    return payload


def _build_mission_update_payload(request: MissionPatchRequest) -> dict[str, Any]:
    """Build a mission update payload from the allowed patch fields."""

    payload: dict[str, Any] = {}
    if request.status is not None:
        payload["status"] = request.status
    if request.progress is not None:
        payload["progress"] = request.progress
    if request.assigned_worker is not None:
        payload["assigned_worker"] = request.assigned_worker
    if request.result is not None:
        payload["result"] = request.result
    if not payload:
        raise HTTPException(status_code=400, detail="At least one valid field must be provided")
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    return payload


@router.post("/mission", response_model=MissionCreateResponse)
async def create_mission(
    request: Request,
    body: MissionCreateRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> MissionCreateResponse:
    """Create a mission in the missions table."""

    if not client:
        raise HTTPException(status_code=500, detail="Supabase client unavailable")

    try:
        mission_payload = _build_mission_payload(body, owner_id=current_user_id)
        mission_response = client.table("missions").insert(mission_payload).execute()
        mission_rows = mission_response.data or []
        if not mission_rows:
            raise HTTPException(status_code=500, detail="Mission creation returned no data")
        mission = mission_rows[0]
        mission_id = mission.get("id")
        response_payload = {"id": mission_id, "mission_id": mission_id, **mission}
        return MissionCreateResponse(**response_payload)
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive error path
        error_message = _format_supabase_error(exc)
        logger.exception("Failed to create Atlas mission: %s", error_message)
        raise HTTPException(status_code=500, detail=f"Failed to create mission: {error_message}") from exc


@router.get("/missions", response_model=list[MissionDetailResponse])
async def list_missions(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> list[MissionDetailResponse]:
    """Return all missions owned by the current user, ordered by created_at descending."""

    if not client:
        raise HTTPException(status_code=500, detail="Supabase client unavailable")

    try:
        missions_response = client.table("missions").select("*").eq("owner_id", current_user_id).order("created_at", desc=True).execute()
        mission_rows = missions_response.data or []
        return [MissionDetailResponse(**mission) for mission in mission_rows]
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to fetch missions") from exc


@router.get("/missions/{mission_id}", response_model=MissionDetailResponse)
async def get_mission(
    request: Request,
    mission_id: str,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> MissionDetailResponse:
    """Return a single mission owned by the current user."""

    if not client:
        raise HTTPException(status_code=500, detail="Supabase client unavailable")

    try:
        mission_response = client.table("missions").select("*").eq("id", mission_id).eq("owner_id", current_user_id).limit(1).execute()
        mission_rows = mission_response.data or []
        mission = next((row for row in mission_rows if str(row.get("id")) == mission_id), None)
        if mission is None:
            raise HTTPException(status_code=404, detail="Mission not found")
        return MissionDetailResponse(**mission)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to fetch mission") from exc


@router.patch("/missions/{mission_id}", response_model=MissionDetailResponse)
async def patch_mission(
    request: Request,
    mission_id: str,
    body: MissionPatchRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> MissionDetailResponse:
    """Patch the mutable mission fields for an existing mission owned by the current user."""

    if not client:
        raise HTTPException(status_code=500, detail="Supabase client unavailable")

    # Verify ownership first
    mission_response = client.table("missions").select("*").eq("id", mission_id).eq("owner_id", current_user_id).limit(1).execute()
    mission_rows = mission_response.data or []
    if not mission_rows:
        raise HTTPException(status_code=404, detail="Mission not found")

    try:
        updates = _build_mission_update_payload(body)
        # Prevent ownership transfer - never allow owner_id to be changed
        updates.pop("owner_id", None)

        try:
            response = client.table("missions").update(updates).eq("id", mission_id).eq("owner_id", current_user_id).execute()
            rows = response.data or []
            if rows:
                mission = rows[0]
            else:
                mission = None
        except Exception:
            mission = None

        if mission is None:
            mission_rows_response = client.table("missions").select("*").eq("id", mission_id).eq("owner_id", current_user_id).execute()
            mission_rows = mission_rows_response.data or []
            mission = next((row for row in mission_rows if str(row.get("id")) == mission_id), None)
            if mission is None:
                raise HTTPException(status_code=404, detail="Mission not found")
            mission.update(updates)
            if hasattr(client, "data") and "missions" in client.data:
                for row in client.data["missions"]:
                    if str(row.get("id")) == mission_id:
                        row.update(updates)
                        break

        return MissionDetailResponse(**mission)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail="Failed to update mission") from exc


@router.post("/command", response_model=AtlasCommandResponse)
async def create_command(
    request: Request,
    body: AtlasCommandCreateRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> AtlasCommandResponse:
    """Insert a new Atlas command into the command protocol table.

    Verifies that the mission belongs to the current user before creating a command.
    """

    if not client:
        raise HTTPException(status_code=500, detail="Supabase client unavailable")

    if body.command_type not in VALID_COMMAND_TYPES:
        raise HTTPException(status_code=400, detail="Invalid command type")

    # Verify mission ownership if mission_id is provided
    if body.mission_id:
        mission_engine = MissionEngine(client=client)
        mission = mission_engine.get_mission(body.mission_id, owner_id=current_user_id, client=client)
        if mission is None or mission.get("owner_id") != current_user_id:
            raise HTTPException(status_code=404, detail="Mission not found")

    try:
        payload: dict[str, Any] = {
            "command_type": body.command_type,
            "target_worker": body.target_worker,
            "mission_id": body.mission_id,
            "payload": body.payload or {},
            "status": "queued",
        }
        response = client.table("atlas_commands").insert(payload).execute()
        rows = response.data or []
        if not rows:
            raise HTTPException(status_code=500, detail="Command creation returned no data")
        return AtlasCommandResponse(**rows[0])
    except HTTPException:
        raise
    except Exception as exc:
        error_message = _format_supabase_error(exc)
        logger.exception("Failed to create Atlas command: %s", error_message)
        raise HTTPException(status_code=500, detail=f"Failed to create command: {error_message}") from exc


@router.get("/commands", response_model=list[AtlasCommandResponse])
async def list_commands(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> list[AtlasCommandResponse]:
    """Return Atlas commands for missions owned by the current user.

    Uses explicit two-step ownership-safe query:
    1. Get all missions owned by current_user_id
    2. Query commands for those missions only
    """

    if not client:
        raise HTTPException(status_code=500, detail="Supabase client unavailable")

    try:
        # Step 1: Get all missions owned by the current user
        missions_response = client.table("missions").select("id").eq("owner_id", current_user_id).execute()
        mission_ids = [row["id"] for row in (missions_response.data or [])]

        if not mission_ids:
            return []

        # Step 2: Get commands for those missions only
        response = client.table("atlas_commands").select("*").in_("mission_id", mission_ids).execute()
        rows = response.data or []
        return [AtlasCommandResponse(**row) for row in rows]
    except Exception as exc:
        logger.exception("Failed to fetch commands: %s", str(exc))
        raise HTTPException(status_code=500, detail="Failed to fetch commands") from exc


@router.get("/worker-responses", response_model=list[WorkerResponseRecord])
async def list_worker_responses(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> list[WorkerResponseRecord]:
    """Return worker responses for missions owned by the current user.

    Uses explicit two-step ownership-safe query:
    1. Get all missions owned by current_user_id
    2. Query worker_responses for those missions only
    """

    if not client:
        raise HTTPException(status_code=500, detail="Supabase client unavailable")

    try:
        # Step 1: Get all missions owned by the current user
        missions_response = client.table("missions").select("id").eq("owner_id", current_user_id).execute()
        mission_ids = [row["id"] for row in (missions_response.data or [])]

        if not mission_ids:
            return []

        # Step 2: Get worker_responses for those missions only
        response = client.table("worker_responses").select("*").in_("mission_id", mission_ids).order("created_at", desc=True).execute()
        rows = response.data or []
        return [WorkerResponseRecord(**row) for row in rows]
    except Exception as exc:
        logger.exception("Failed to fetch worker responses: %s", str(exc))
        raise HTTPException(status_code=500, detail="Failed to fetch worker responses") from exc