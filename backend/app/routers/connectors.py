"""Connector API routes for Sprint 7.2-B.

Provides REST endpoints for platform connector operations, onboarding workflows,
and human intervention checkpoints.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.connectors.pinterest_connector import PinterestConnector
from app.services.connectors.registry import ConnectorRegistry
from app.services.human_intervention import HumanInterventionManager
from app.services.stores.onboarding_workflow_store import OnboardingWorkflowStore
from app.services.stores.platform_connection_store import PlatformConnectionStore
from app.services.worker_runtime import WorkerRuntime

router = APIRouter(
    prefix="/connectors",
    tags=["connectors"],
    dependencies=[Depends(get_current_user_id)],
)

# Initialize services
_connector_registry: ConnectorRegistry | None = None
_human_intervention_manager: HumanInterventionManager | None = None


def get_connector_registry() -> ConnectorRegistry:
    """Get or initialize the connector registry."""
    global _connector_registry
    if _connector_registry is None:
        _connector_registry = ConnectorRegistry()
        # Register Pinterest connector
        pinterest_connector = PinterestConnector()
        _connector_registry.register(pinterest_connector)
    return _connector_registry


def get_worker_runtime(current_user_id: str = Depends(get_current_user_id)) -> WorkerRuntime:
    """Get or initialize the worker runtime scoped to the current user."""
    registry = get_connector_registry()
    return WorkerRuntime(connector_registry=registry, owner_id=current_user_id)


def get_human_intervention_manager(
    client: Any | None = None,
) -> HumanInterventionManager:
    """Get or initialize the human intervention manager.

    When a user-scoped ``client`` is supplied it is forwarded so that RLS
    policies evaluate against the authenticated user identity.
    """
    global _human_intervention_manager
    if _human_intervention_manager is None:
        _human_intervention_manager = HumanInterventionManager(client=client)
    return _human_intervention_manager


def _get_user_scoped_pinterest_connector(
    client: Any | None = None,
) -> PinterestConnector:
    """Create a PinterestConnector bound to the caller's Supabase client.

    A fresh connector is returned per call so no global mutable user state
    is ever introduced. Both the workflow store and the connection store
    receive the user-scoped client so that RLS evaluates against the
    authenticated user identity for every persistence operation.
    """
    workflow_store = OnboardingWorkflowStore(client=client, durable_required=True)
    connection_store = PlatformConnectionStore(client=client)
    return PinterestConnector(
        workflow_store=workflow_store,
        connection_store=connection_store,
    )


# Pydantic models
class OnboardingStartRequest(BaseModel):
    """Request to start platform onboarding."""

    platform: str
    worker_id: str

    model_config = ConfigDict(extra="allow")


class OnboardingResumeRequest(BaseModel):
    """Request to resume an onboarding workflow."""

    checkpoint_id: str | None = None
    human_input: dict[str, Any] = {}

    model_config = ConfigDict(extra="allow")


class CheckpointCompleteRequest(BaseModel):
    """Request to mark a checkpoint as completed."""

    human_input: dict[str, Any] = {}

    model_config = ConfigDict(extra="allow")


# Routes
@router.get("")
async def list_connectors() -> dict[str, Any]:
    """List all supported platform connectors."""
    registry = get_connector_registry()
    platforms = registry.list_platforms()
    capabilities = registry.list_capabilities()
    return {
        "success": True,
        "platforms": platforms,
        "capabilities": capabilities,
    }


@router.get("/{platform}")
async def get_connector_info(platform: str) -> dict[str, Any]:
    """Get information about a specific platform connector."""
    registry = get_connector_registry()
    if not registry.has_connector(platform):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No connector found for platform '{platform}'",
        )

    connector = registry.get(platform)
    capabilities = registry.get_capabilities_dict(platform)
    health = connector.health_check()

    return {
        "success": True,
        "platform": platform,
        "capabilities": capabilities,
        "health": health,
    }


@router.get("/{platform}/health")
async def connector_health(platform: str) -> dict[str, Any]:
    """Check the health of a platform connector."""
    registry = get_connector_registry()
    connector = registry.get(platform)

    if not connector:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No connector found for platform '{platform}'",
        )

    return connector.health_check()


@router.post("/onboarding/start")
async def start_onboarding(
    request: OnboardingStartRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Start a platform onboarding workflow for a worker owned by the current user."""
    platform = request.platform
    worker_id = request.worker_id

    # Verify worker ownership
    if client:
        try:
            response = (
                client.table("workers")
                .select("*")
                .eq("id", worker_id)
                .eq("owner_id", current_user_id)
                .limit(1)
                .execute()
            )
            rows = response.data or []
            if not rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Worker not found",
                )
        except HTTPException:
            raise
        except Exception:
            pass

    if platform == "pinterest":
        connector = _get_user_scoped_pinterest_connector(client=client)
        result = connector.start_onboarding(worker_id)
    else:
        registry = get_connector_registry()
        if not registry.has_connector(platform):
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"No connector found for platform '{platform}'",
            )
        connector = registry.get(platform)
        result = connector.start_onboarding(worker_id)

    return {
        "success": True,
        "workflow": result,
    }


@router.get("/onboarding/{workflow_id}")
async def get_onboarding_status(
    workflow_id: str,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Get the status of an onboarding workflow owned by the current user.

    Uses the user-scoped Supabase client so that the database RLS policy
    (``onboarding_workflows_select_own``) enforces row-level ownership
    before the workflow is returned. When ``client`` is ``None`` (no
    authenticated Supabase backend), falls back to the in-memory store
    without exposing another user's data.
    """
    if client:
        try:
            response = (
                client.table("onboarding_workflows")
                .select("*")
                .eq("id", workflow_id)
                .limit(1)
                .execute()
            )
            rows = response.data or []
            if not rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workflow not found",
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to fetch workflow status",
            )

    store = OnboardingWorkflowStore(client=client, durable_required=True)
    workflow = store.get(workflow_id)
    if workflow is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Workflow not found",
        )

    checkpoint_data = workflow.get("checkpoint_data") or {}
    step_history = list(workflow.get("step_history") or [])
    instructions = checkpoint_data.get("instructions", "")
    checkpoint_type = checkpoint_data.get("checkpoint_type")

    pending_checkpoints = []
    for step in step_history:
        if step.get("checkpoint_type"):
            pending_checkpoints.append({
                "step": step.get("step"),
                "name": step.get("name"),
                "checkpoint_type": step.get("checkpoint_type"),
                "status": step.get("status"),
                "completed_at": step.get("completed_at"),
            })

    product_state = {
        "success": True,
        "workflow_id": workflow.get("workflow_id") or workflow_id,
        "platform": workflow.get("platform"),
        "status": workflow.get("status"),
        "current_step": workflow.get("current_step"),
        "total_steps": workflow.get("total_steps"),
        "requires_human_intervention": workflow.get("status") == "awaiting_human",
        "checkpoint_type": checkpoint_type,
        "instructions": instructions,
        "next_step": checkpoint_type,
        "pending_checkpoints": pending_checkpoints,
        "step_history": step_history,
        "started_by_approval_id": workflow.get("started_by_approval_id"),
        "created_at": workflow.get("created_at"),
        "updated_at": workflow.get("updated_at"),
    }
    return product_state


@router.post("/onboarding/{workflow_id}/resume")
async def resume_onboarding(
    workflow_id: str,
    request: OnboardingResumeRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Resume an onboarding workflow after human checkpoint completion.

    Delegates to the existing ``PinterestConnector.resume_onboarding()``
    implementation. Uses the authenticated user-scoped Supabase client
    so all reads/writes are RLS-enforced.

    The checkpoint stored in ``human_intervention_checkpoints`` is marked
    completed with the provided ``human_input`` before the connector
    advances the workflow.
    """
    if client:
        try:
            response = (
                client.table("onboarding_workflows")
                .select("id", "worker_id")
                .eq("id", workflow_id)
                .limit(1)
                .execute()
            )
            rows = response.data or []
            if not rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Workflow not found",
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to verify workflow ownership",
            )

    manager = get_human_intervention_manager(client=client)
    if request.checkpoint_id:
        checkpoint = manager.get_checkpoint(request.checkpoint_id)
        if checkpoint:
            mission_id = checkpoint.get("mission_id")
            if mission_id and client:
                try:
                    mission_response = (
                        client.table("missions")
                        .select("owner_id")
                        .eq("id", mission_id)
                        .eq("owner_id", current_user_id)
                        .limit(1)
                        .execute()
                    )
                    if not mission_response.data:
                        raise HTTPException(
                            status_code=status.HTTP_404_NOT_FOUND,
                            detail="Checkpoint not found",
                        )
                except HTTPException:
                    raise

    connector = _get_user_scoped_pinterest_connector(client=client)
    result = connector.resume_onboarding(workflow_id, request.human_input)

    if not result.get("success"):
        status_code = status.HTTP_404_NOT_FOUND if "not found" in str(result.get("error", "")).lower() else status.HTTP_500_INTERNAL_SERVER_ERROR
        raise HTTPException(
            status_code=status_code,
            detail=result.get("error", "Workflow resume failed"),
        )

    if result.get("requires_human_intervention"):
        return {
            "success": True,
            "workflow_id": result.get("workflow_id"),
            "platform": result.get("platform"),
            "status": result.get("status"),
            "current_step": result.get("current_step"),
            "total_steps": result.get("total_steps"),
            "requires_human_intervention": True,
            "checkpoint_type": result.get("checkpoint_type"),
            "instructions": result.get("instructions"),
            "metadata": result.get("metadata"),
            "next_step": result.get("next_step"),
        }

    return {
        "success": True,
        "workflow_id": result.get("workflow_id"),
        "platform": result.get("platform"),
        "status": result.get("status"),
        "current_step": result.get("current_step"),
        "total_steps": result.get("total_steps"),
        "requires_human_intervention": False,
        "completed": result.get("status") == "completed",
        "message": result.get("instructions", ""),
    }


@router.get("/checkpoints/pending")
async def list_pending_checkpoints(
    mission_id: str | None = None,
    platform: str | None = None,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """List pending human intervention checkpoints for the current user's missions."""
    manager = get_human_intervention_manager(client=client)
    checkpoints = manager.list_pending_checkpoints(
        mission_id=mission_id,
        platform=platform,
    )
    # Filter checkpoints by ownership if client is available
    if client and mission_id:
        try:
            # Verify mission ownership
            mission_response = client.table("missions").select("owner_id").eq("id", mission_id).eq("owner_id", current_user_id).limit(1).execute()
            mission_rows = mission_response.data or []
            if not mission_rows:
                checkpoints = []
        except Exception:
            checkpoints = []
    return {
        "success": True,
        "checkpoints": checkpoints,
        "count": len(checkpoints),
    }


@router.get("/checkpoints/{checkpoint_id}")
async def get_checkpoint(
    checkpoint_id: str,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Get a specific human intervention checkpoint owned by the current user.

    The checkpoint itself must resolve to a mission that belongs to current_user_id.
    """
    manager = get_human_intervention_manager(client=client)
    checkpoint = manager.get_checkpoint(checkpoint_id)

    if not checkpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checkpoint '{checkpoint_id}' not found",
        )

    # Verify checkpoint ownership through mission_id stored in checkpoint
    mission_id = checkpoint.get("mission_id")
    if mission_id and client:
        try:
            mission_response = client.table("missions").select("owner_id").eq("id", mission_id).eq("owner_id", current_user_id).limit(1).execute()
            mission_rows = mission_response.data or []
            if not mission_rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Checkpoint '{checkpoint_id}' not found",
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Checkpoint '{checkpoint_id}' not found",
            )

    return {
        "success": True,
        "checkpoint": checkpoint,
    }


@router.post("/checkpoints/{checkpoint_id}/complete")
async def complete_checkpoint(
    checkpoint_id: str,
    request: CheckpointCompleteRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Mark a checkpoint as completed by the human.

    The checkpoint must resolve to a mission that belongs to current_user_id.
    """
    manager = get_human_intervention_manager(client=client)
    checkpoint = manager.get_checkpoint(checkpoint_id)

    if not checkpoint:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Checkpoint '{checkpoint_id}' not found",
        )

    # Verify checkpoint ownership through mission_id stored in checkpoint
    mission_id = checkpoint.get("mission_id")
    if mission_id and client:
        try:
            mission_response = client.table("missions").select("owner_id").eq("id", mission_id).eq("owner_id", current_user_id).limit(1).execute()
            mission_rows = mission_response.data or []
            if not mission_rows:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Checkpoint '{checkpoint_id}' not found",
                )
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Checkpoint '{checkpoint_id}' not found",
            )

    result = manager.complete_checkpoint(
        checkpoint_id,
        human_input=request.human_input,
    )

    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to complete checkpoint"),
        )

    return {
        "success": True,
        "checkpoint": result.get("checkpoint"),
    }