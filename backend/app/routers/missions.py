"Mission API routes for Sprint 6.0."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from app import database as database_module
from app.dependencies import get_current_user_id, get_user_scoped_client, verify_mission_ownership
from app.services.agent_orchestrator import AgentOrchestrator
from app.services.employee_vertical_slice import EmployeeVerticalSlice
from app.services.mission_engine import MissionEngine

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/missions", tags=["missions"])


class MissionCreateRequest(BaseModel):
    """Request payload for creating a mission."""

    title: str
    description: str | None = None
    worker_id: str | None = None
    priority: str | None = None

    model_config = ConfigDict(extra="allow")


class ObjectiveRunRequest(BaseModel):
    """Structured user goal for the P1-7B employee slice."""

    goal: str
    mission_id: str | None = None
    metadata: dict[str, Any] | None = None


def _status_code_for_error(error: str | None) -> int:
    """Translate a service-layer error into an HTTP status code."""

    if not error:
        return status.HTTP_500_INTERNAL_SERVER_ERROR

    message = error.lower()
    if "required" in message or "missing" in message:
        return status.HTTP_400_BAD_REQUEST
    if "not found" in message:
        return status.HTTP_404_NOT_FOUND
    if "unavailable" in message or "client" in message:
        return status.HTTP_503_SERVICE_UNAVAILABLE
    return status.HTTP_500_INTERNAL_SERVER_ERROR


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_mission(
    request: Request,
    body: MissionCreateRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Create a mission for the current user."""

    # Repair/register the selected worker before creating a mission. Existing
    # workers may predate worker_registry and otherwise fail the missions FK.
    if body.worker_id:
        if not client:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supabase client unavailable")
        try:
            worker_response = (
                client.table("workers")
                .select("id,name,role,status,owner_id")
                .eq("id", body.worker_id)
                .eq("owner_id", current_user_id)
                .limit(1)
                .execute()
            )
            worker_rows = worker_response.data or []
            if not worker_rows:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Selected worker was not found for this account")
            worker = worker_rows[0]
            registry_payload = {
                "id": str(worker["id"]),
                "name": worker.get("name") or "AEA Worker",
                "worker_type": worker.get("role") or "assistant",
                "platform": "pinterest",
                "status": worker.get("status") or "active",
                "capabilities": ["affiliate_marketing", "pinterest_onboarding"],
            }
            # This protected registry write is performed server-side only after
            # verifying that the selected worker belongs to the authenticated user.
            registry_client = database_module.get_supabase_service_client()
            if not registry_client:
                raise RuntimeError("Server-side registry client is not configured")
            registry_client.table("worker_registry").upsert(
                registry_payload, on_conflict="id"
            ).execute()
        except HTTPException:
            raise
        except Exception as exc:
            logger.exception("Could not register selected worker before mission creation")
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Could not register the selected worker before mission creation.",
            ) from exc

    mission_engine = MissionEngine(client=client)
    result = mission_engine.create_mission(
        title=body.title,
        description=body.description or "",
        worker_id=body.worker_id,
        priority=body.priority or "normal",
        owner_id=current_user_id,
        client=client,
    )

    if not result.get("success"):
        error = result.get("error") or "Failed to create mission"
        raise HTTPException(status_code=_status_code_for_error(str(error)), detail=str(error))

    mission = result.get("mission") or {}
    return {"success": True, "mission": mission}


@router.get("")
def list_missions(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> list[dict[str, Any]]:
    """Return mission records owned by the authenticated user.

    Use the caller-scoped client first. If RLS/client session propagation
    unexpectedly yields no rows, retry with the server client but retain an
    explicit owner_id filter. Never return another user's missions.
    """

    if not client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase user-scoped client is unavailable.",
        )

    mission_engine = MissionEngine(client=client)

    try:
        response = (
            client.table("missions")
            .select("*")
            .eq("owner_id", current_user_id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        rows = response.data or []
        if rows:
            return [
                normalized
                for row in rows
                if (normalized := mission_engine._normalize_mission(row)) is not None
            ]
    except Exception:
        logger.warning("User-scoped mission listing failed; attempting owner-scoped server fallback")

    # The user identity is verified by get_current_user_id. The fallback is
    # constrained by that canonical auth.users UUID, not a user-supplied owner.
    try:
        service_client = database_module.get_supabase_service_client()
        if not service_client:
            raise RuntimeError("Server-side Supabase client is not configured")
        response = (
            service_client.table("missions")
            .select("*")
            .eq("owner_id", current_user_id)
            .order("created_at", desc=True)
            .limit(100)
            .execute()
        )
        rows = response.data or []
        return [
            normalized
            for row in rows
            if (normalized := mission_engine._normalize_mission(row)) is not None
        ]
    except Exception as exc:
        logger.exception("Owner-scoped mission listing failed")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not load missions for this account. Please refresh and try again.",
        ) from exc


@router.get("/{mission_id}")
async def get_mission(
    mission_id: str,
    mission: dict[str, Any] = Depends(verify_mission_ownership),
) -> dict[str, Any]:
    """Return one mission owned by the current user."""
    return mission


@router.post("/objective")
async def run_objective(
    request: Request,
    body: ObjectiveRunRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Run one authenticated objective through the P1-7B employee flow."""

    employee = EmployeeVerticalSlice(owner_id=current_user_id, client=client)
    return employee.run(body.goal, mission_id=body.mission_id, metadata=body.metadata)


@router.post("/{mission_id}/run")
async def run_mission(
    request: Request,
    mission_id: str,
    mission: dict[str, Any] = Depends(verify_mission_ownership),
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Execute a mission owned by the current user."""

    # Persist lifecycle transitions so the authenticated UI does not remain stuck
    # on the previous "active" value after the run action returns.
    mission_engine = MissionEngine(client=client)
    started = mission_engine.update_status(mission_id, "active", client=client)
    if not started.get("success"):
        logger.error("Could not mark mission %s active before execution", mission_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not record mission start. Please refresh and try again.",
        )

    orchestrator = AgentOrchestrator(owner_id=current_user_id, client=client)
    result = orchestrator.run_mission(mission_id)
    if not result.get("success"):
        error = str(result.get("error") or "Mission execution failed")
        failed = mission_engine.fail_mission(mission_id, error, client=client)
        if not failed.get("success"):
            logger.error("Mission %s failed and its failure status could not be persisted", mission_id)
        raise HTTPException(status_code=_status_code_for_error(error), detail=error)

    execution = result.get("execution") or {}
    completed = mission_engine.complete_mission(
        mission_id, result, client=client, owner_id=current_user_id
    )
    if not completed.get("success"):
        logger.error(
            "Mission %s executed but completion state could not be persisted: %s",
            mission_id,
            completed.get("error") or "unknown persistence error",
        )
        return {
            **result,
            "lifecycle_persisted": False,
            "warning": "Execution returned, but the mission status could not be saved. Refresh before retrying.",
        }

    return {
        **result,
        "mission": completed.get("mission") or mission,
        "lifecycle_persisted": True,
        "status": (completed.get("mission") or {}).get("status") or "completed",
    }
