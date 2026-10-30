"""Worker API routes for Sprint 6.0."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from app import database as database_module
from app.dependencies import get_current_user_id, get_user_scoped_client, verify_ownership
from app.services.agent_orchestrator import AgentOrchestrator
from app.services.worker_runtime import WorkerRuntime

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/workers", tags=["workers"])


class WorkerCreateRequest(BaseModel):
    """Request payload for creating a worker."""

    name: str
    role: str | None = None
    status: str | None = None

    model_config = ConfigDict(extra="allow")


# Mock data for when Supabase is unavailable
MOCK_WORKERS = [
    {
        "id": "worker-atlas",
        "name": "Atlas",
        "role": "researcher",
        "status": "active",
    },
    {
        "id": "worker-sentinel",
        "name": "Sentinel",
        "role": "analyst",
        "status": "active",
    },
]


@router.get("")
def list_workers(
    request: Request,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> list[dict[str, Any]]:
    """Return workers owned by the current user."""

    if not client:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Supabase user-scoped client is unavailable. Workers were not loaded.",
        )

    try:
        response = client.table("workers").select("*").eq("owner_id", current_user_id).execute()
        rows = response.data or []
        return [dict(row) for row in rows]
    except Exception as exc:  # pragma: no cover - defensive error path
        # Never disguise a database/ownership failure as a successful mock response.
        logger.exception("Could not load workers for authenticated owner %s", current_user_id)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Could not load workers for this account. Please refresh and try again.",
        ) from exc


@router.post("")
async def create_worker(
    request: Request,
    body: WorkerCreateRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Create a new worker owned by the current user."""

    if not client:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supabase client unavailable")

    try:
        payload = {
            "name": body.name,
            "role": body.role or "assistant",
            "status": body.status or "active",
            "owner_id": current_user_id,
        }
        response = client.table("workers").insert(payload).execute()
        record = response.data[0] if response.data else None
        if not record:
            raise RuntimeError("Worker creation returned no record")

        # Missions.assigned_worker references worker_registry.id, while the
        # user-facing workers API persists records in workers. Keep the IDs
        # aligned so a mission created for this worker satisfies the FK.
        worker_id = str(record.get("id") or "")
        if not worker_id:
            raise RuntimeError("Created worker has no ID; cannot register it for missions")
        registry_payload = {
            "id": worker_id,
            "name": record.get("name") or body.name,
            "worker_type": record.get("role") or body.role or "assistant",
            "platform": "pinterest",
            "status": record.get("status") or body.status or "active",
            "capabilities": ["affiliate_marketing", "pinterest_onboarding"],
        }
        try:
            client.table("worker_registry").upsert(registry_payload, on_conflict="id").execute()
        except Exception as registry_exc:
            raise RuntimeError(
                "Worker was created but could not be registered in worker_registry, "
                "which is required before creating missions: " + str(registry_exc)
            ) from registry_exc

        return {"success": True, "worker": record}
    except Exception as exc:  # pragma: no cover - defensive error path
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc


@router.get("/{worker_id}/status")
async def get_worker_status(
    worker_id: str,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Return the runtime state for a worker owned by the current user."""

    if not client:
        return {"success": False, "error": "Supabase client unavailable"}

    # Verify worker ownership
    try:
        response = client.table("workers").select("*").eq("id", worker_id).eq("owner_id", current_user_id).limit(1).execute()
        rows = response.data or []
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    worker_runtime = WorkerRuntime(owner_id=current_user_id)
    state = worker_runtime.get_worker_state(worker_id)
    return state


@router.post("/{worker_id}/run")
async def run_worker(
    worker_id: str,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Execute all pending missions for a worker owned by the current user."""

    if not client:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="Supabase client unavailable")

    # Verify worker ownership
    try:
        response = client.table("workers").select("*").eq("id", worker_id).eq("owner_id", current_user_id).limit(1).execute()
        rows = response.data or []
        if not rows:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Worker not found")
    except HTTPException:
        raise
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)) from exc

    orchestrator = AgentOrchestrator(owner_id=current_user_id, client=client)
    result = orchestrator.run_worker(worker_id)
    # A worker run can be processed successfully while individual missions fail.
    # Return the complete execution report so callers can distinguish completed,
    # partial, and failed batches instead of losing counts/results in an HTTP 500.
    return result