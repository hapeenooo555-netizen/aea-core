"""Authenticated P1-8 AI Employee orchestration API."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.agent_runtime import AEAAgentRuntime
from app.services.agent_runtime_contract import RuntimeRequest
from app.services.mission_orchestration import MissionOrchestrationService

router = APIRouter(prefix="/employee", tags=["employee"])


class ObjectiveRequest(BaseModel):
    objective: str
    title: str | None = None
    priority: str = "normal"
    urgency: str = "normal"
    business_importance: int = Field(default=1, ge=1, le=5)
    scheduled_at: datetime | None = None
    recurrence: str | None = None
    dependencies: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    idempotency_key: str | None = None
    run_now: bool = False
    background: bool = False
    locale: str = "en"
    timezone: str = "UTC"
    success_criteria: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    requested_capabilities: list[str] = Field(default_factory=list)

    model_config = ConfigDict(extra="forbid")


class ScheduleRequest(BaseModel):
    scheduled_at: datetime
    recurrence: str | None = None

    model_config = ConfigDict(extra="forbid")


def _service(owner_id: str, client: Any) -> MissionOrchestrationService:
    return MissionOrchestrationService(owner_id, client=client)


def _raise(result: dict[str, Any]) -> None:
    if result.get("success"):
        return
    error = str(result.get("error") or "Employee operation failed")
    code = status.HTTP_404_NOT_FOUND if "not found" in error.lower() else status.HTTP_400_BAD_REQUEST
    raise HTTPException(status_code=code, detail=error)


@router.post("/objective", status_code=status.HTTP_201_CREATED)
def create_objective(
    body: ObjectiveRequest,
    background_tasks: BackgroundTasks,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Turn an authenticated objective into a durable employee mission."""
    if body.background and body.run_now:
        raise HTTPException(status_code=422, detail="Choose background or immediate execution.")
    if body.background and client is None:
        raise HTTPException(status_code=503, detail="Authenticated task storage is unavailable.")
    request = RuntimeRequest(
        owner_id=owner_id,
        objective=body.objective,
        request_id=body.idempotency_key or str(uuid4()),
        locale=body.locale,
        timezone=body.timezone,
        success_criteria=tuple(body.success_criteria),
        constraints=body.constraints,
        requested_capabilities=tuple(body.requested_capabilities),
    )
    runtime = AEAAgentRuntime(owner_id, client=client)
    result = runtime.submit(
        request,
        run_now=body.run_now,
        mission_options={
            "title": body.title,
            "priority": body.priority,
            "urgency": body.urgency,
            "business_importance": body.business_importance,
            "scheduled_at": body.scheduled_at.isoformat() if body.scheduled_at else None,
            "recurrence": body.recurrence,
            "dependencies": body.dependencies,
            "metadata": {**body.metadata, "background_dispatch": body.background},
        },
    )
    if not result.get("success"):
        errors = result.get("errors") or [result.get("error") or "Employee operation failed"]
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=errors)
    mission_id = result.get("mission_id")
    mission = _service(owner_id, client).get_mission(mission_id) if mission_id else None
    if mission is None:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Runtime accepted the request but mission could not be read back")
    if body.background:
        _schedule_background(_service(owner_id, client), mission, background_tasks)
    # Preserve the legacy response shape while exposing the canonical runtime envelope.
    return {"success": True, "mission": mission, "runtime": result}


def _can_resume(service: MissionOrchestrationService, mission: dict[str, Any]) -> bool:
    return bool((mission.get("metadata") or {}).get("background_dispatch") is True
                and service.readiness(mission)["ready"])


def _schedule_background(service: MissionOrchestrationService, mission: dict[str, Any],
                         background_tasks: BackgroundTasks) -> bool:
    if not _can_resume(service, mission):
        return False
    # The service claims the durable owner-scoped lease before executing tools.
    background_tasks.add_task(service.run_mission, mission["id"])
    return True


@router.get("/missions")
async def list_employee_missions(
    status_filter: str | None = Query(default=None, alias="status"),
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    return {"success": True, "missions": _service(owner_id, client).list_missions(status=status_filter)}


@router.get("/missions/next")
async def next_employee_mission(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    return _service(owner_id, client).select_next()


@router.get("/missions/{mission_id}")
async def get_employee_mission(
    mission_id: str,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    service = _service(owner_id, client)
    mission = service.get_mission(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return {"success": True, "mission": mission, "can_resume": _can_resume(service, mission)}


@router.post("/missions/{mission_id}/dispatch", status_code=status.HTTP_202_ACCEPTED)
def dispatch_employee_mission(
    mission_id: str,
    background_tasks: BackgroundTasks,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    if client is None:
        raise HTTPException(status_code=503, detail="Authenticated task storage is unavailable.")
    service = _service(owner_id, client)
    mission = service.get_mission(mission_id)
    if not mission:
        raise HTTPException(status_code=404, detail="Mission not found")
    return {"success": True, "scheduled": _schedule_background(service, mission, background_tasks)}


@router.post("/missions/{mission_id}/run")
def run_employee_mission(
    mission_id: str,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    result = _service(owner_id, client).run_mission(mission_id)
    _raise(result)
    return result


@router.post("/missions/{mission_id}/pause")
async def pause_employee_mission(
    mission_id: str,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    result = _service(owner_id, client).pause(mission_id)
    _raise(result)
    return result


@router.post("/missions/{mission_id}/resume")
async def resume_employee_mission(
    mission_id: str,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    result = _service(owner_id, client).resume(mission_id)
    _raise(result)
    return result


@router.post("/missions/{mission_id}/schedule")
async def schedule_employee_mission(
    mission_id: str,
    body: ScheduleRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    result = _service(owner_id, client).schedule(mission_id, body.scheduled_at.isoformat(), body.recurrence)
    _raise(result)
    return result


@router.post("/missions/{mission_id}/cancel")
async def cancel_employee_mission(
    mission_id: str,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    result = _service(owner_id, client).cancel(mission_id)
    _raise(result)
    return result


@router.get("/status")
async def employee_status(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    service = _service(owner_id, client)
    return {"success": True, "owner_id": owner_id, "next": service.select_next(), "report": service.report()}


@router.get("/report")
async def employee_report(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    return {"success": True, "report": _service(owner_id, client).report()}


@router.get("/missions/{mission_id}/events")
async def mission_events(
    mission_id: str,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    service = _service(owner_id, client)
    if not service.get_mission(mission_id):
        raise HTTPException(status_code=404, detail="Mission not found")
    return {"success": True, "events": service.events(mission_id=mission_id)}


__all__ = ["router"]