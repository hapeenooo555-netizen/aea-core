"""Connector API routes for Sprint 7.2-B.

Provides REST endpoints for platform connector operations, onboarding workflows,
and human intervention checkpoints.
"""

from __future__ import annotations

import httpx
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.connectors.pinterest_connector import PinterestConnector
from app.services.connectors.registry import ConnectorRegistry
from app.services.human_intervention import HumanInterventionManager
from app.services.pinterest_oauth import PinterestOAuthConfig, PinterestOAuthHelper
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
    owner_id_uuid: str | None = None,
) -> PinterestConnector:
    """Create a PinterestConnector bound to the caller's Supabase client.

    A fresh connector is returned per call so no global mutable user state
    is ever introduced. Both the workflow store and the connection store
    receive the user-scoped client so that RLS evaluates against the
    authenticated user identity for every persistence operation.
    """
    workflow_store = OnboardingWorkflowStore(client=client, durable_required=True)
    connection_store = PlatformConnectionStore(
        client=client,
        owner_id_uuid=owner_id_uuid,
    )
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
        connector = _get_user_scoped_pinterest_connector(
            client=client,
            owner_id_uuid=current_user_id,
        )
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

    When ``checkpoint_id`` is supplied the checkpoint owned by the current
    user is verified for ownership first. The connector's
    ``resume_onboarding()`` runs before the checkpoint is marked completed
    so that a failed resume never records a false "completed" checkpoint.
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

    connector = _get_user_scoped_pinterest_connector(
        client=client,
        owner_id_uuid=current_user_id,
    )
    result = connector.resume_onboarding(workflow_id, request.human_input)

    if not result.get("success"):
        status_code = status.HTTP_404_NOT_FOUND if "not found" in str(result.get("error", "")).lower() else status.HTTP_500_INTERNAL_SERVER_ERROR
        raise HTTPException(
            status_code=status_code,
            detail=result.get("error", "Workflow resume failed"),
        )

    # Only mark the checkpoint as completed when the workflow actually advanced.
    # If resume_onboarding failed above we never reach this point.
    if request.checkpoint_id:
        manager.complete_checkpoint(
            request.checkpoint_id,
            human_input=request.human_input,
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


@router.get("/oauth/callback")
async def pinterest_oauth_callback(
    code: str | None = None,
    state: str | None = None,
    error: str | None = None,
    error_description: str | None = None,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Pinterest OAuth 2.0 callback endpoint (GET).

    Receives the authorization ``code`` and CSRF ``state`` as query
    parameters from Pinterest's redirect.  Validates the state against
    the stored checkpoint, exchanges the code for access/refresh tokens,
    persists the connection, and resumes the onboarding workflow.

    The cryptographically random persisted OAuth state is the trusted
    boundary. The callback relies on it to determine which checkpoint
    to process and whether this callback may perform the token exchange.

    Args:
        code: Authorization code from Pinterest (never stored or logged).
        state: CSRF state token to validate.
        error: OAuth error code from Pinterest (if denied).
        error_description: OAuth error description (if denied).
        current_user_id: Authenticated user identity (from JWT via FastAPI Depends).
        client: User-scoped Supabase client (RLS-enforced).

    Returns:
        Dictionary with connection status and next-step guidance.
    """
    if error:
        return {
            "success": False,
            "status": "denied",
            "error": error,
            "error_description": error_description,
        }

    if not code or not state:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing 'code' or 'state' parameter",
        )

    helper = PinterestOAuthHelper(PinterestOAuthConfig())
    manager = get_human_intervention_manager(client=client)

    # Phase 1: Claim the checkpoint by OAuth state.
    # The returned status determines whether this callback may
    # perform the token exchange.
    #   'claimed'  -> this callback won the claim, may exchange
    #   'processing' -> another callback is already processing
    #   'completed' -> already completed, idempotent result
    #   'failed'    -> definitive failure, new auth required
    #   'unknown'   -> checkpoint not found
    claim_result = manager.claim_oauth_callback(state, owner_id=current_user_id)
    if claim_result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No checkpoint found for this OAuth state",
        )

    claim_status = claim_result.get("status")
    checkpoint = claim_result.get("checkpoint", {})
    checkpoint_id = checkpoint.get("id")

    if claim_status == "processing":
        return {
            "success": False,
            "status": "processing",
            "checkpoint_id": checkpoint_id,
            "message": "OAuth callback is already being processed by another request",
        }

    if claim_status == "completed":
        return {
            "success": True,
            "status": "completed",
            "checkpoint_id": checkpoint_id,
            "message": "OAuth callback already completed",
        }

    if claim_status == "failed":
        return {
            "success": False,
            "status": "failed",
            "checkpoint_id": checkpoint_id,
            "message": "OAuth callback previously failed; new authorization required",
        }

    if claim_status == "unknown":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No pending checkpoint found for this OAuth state",
        )

    # claim_status == 'claimed': this callback won the claim.
    # Only this callback may perform the token exchange.

    # Validate the state one more time for security.
    is_valid, reason = helper.validate_state(
        received_state=state,
        checkpoint=checkpoint,
        owner_id=current_user_id,
    )
    if not is_valid:
        manager.fail_oauth_callback(state, owner_id=current_user_id, reason=reason)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"OAuth state validation failed: {reason}",
        )

    # When OAuth is not configured (e.g. test environments), return
    # the checkpoint-completed state without attempting a network call.
    config = PinterestOAuthConfig()
    if not config.is_configured:
        return {
            "success": True,
            "status": "checkpoint_completed",
            "checkpoint_id": checkpoint_id,
            "message": "Pinterest authorization code received. Token exchange will complete the connection.",
            "next_step": "token_exchange",
        }

    # Phase 2: Exchange authorization code for access/refresh tokens.
    # Only the winning callback performs this exchange.
    token_url = "https://api.pinterest.com/v5/oauth/token"
    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": config.redirect_uri,
    }
    try:
        async with httpx.AsyncClient() as http_client:
            token_response = await http_client.post(
                token_url,
                data=payload,
                auth=(config.client_id, config.client_secret),
                headers={"Accept": "application/json"},
                timeout=30,
            )
        if token_response.status_code != 200:
            # Definitive OAuth rejection: processing -> failed
            manager.fail_oauth_callback(state, owner_id=current_user_id, reason=f"Pinterest returned {token_response.status_code}")
            return {
                "success": False,
                "status": "failed",
                "checkpoint_id": checkpoint_id,
                "error": "pinterest_token_error",
                "error_description": f"Pinterest returned {token_response.status_code}",
            }
        token_data = token_response.json()
    except Exception:  # pragma: no cover - network errors
        # Retryable failure: processing -> awaiting_human
        manager.reconcile_oauth_callback(state, owner_id=current_user_id)
        return {
            "success": False,
            "status": "retryable",
            "checkpoint_id": checkpoint_id,
            "error": "pinterest_token_error",
            "error_description": "Failed to reach Pinterest token endpoint; retry allowed",
        }

    access_token = token_data.get("access_token")
    refresh_token = token_data.get("refresh_token")
    token_type = token_data.get("token_type", "bearer")
    expires_in = token_data.get("expires_in")

    if not access_token:
        # Definitive OAuth rejection: processing -> failed
        manager.fail_oauth_callback(state, owner_id=current_user_id, reason="Missing access token from Pinterest")
        return {
            "success": False,
            "status": "failed",
            "checkpoint_id": checkpoint_id,
            "error": "missing_access_token",
            "error_description": "Pinterest did not return an access token",
        }

    # Normalize scopes from Pinterest token response (space-separated string).
    token_scopes = token_data.get("scope") or token_data.get("scopes")
    if isinstance(token_scopes, str):
        token_scopes = [s.strip() for s in token_scopes.split() if s.strip()]
    elif token_scopes is None:
        token_scopes = []

    # Phase 3: Call the atomic completion RPC.
    # This performs ALL durable operations in ONE PostgreSQL transaction:
    #   1. create/update platform_connections
    #   2. advance onboarding workflow exactly once
    #   3. transition mission waiting_human -> pending
    #   4. record deterministic idempotency information
    #   5. transition checkpoint processing -> completed
    #   6. set oauth_state_consumed = true
    workflow_id = checkpoint.get("metadata", {}).get("workflow_id")
    if not workflow_id:
        # Try legacy nested metadata
        nested = checkpoint.get("metadata", {}).get("metadata", {})
        workflow_id = nested.get("workflow_id")

    if not workflow_id:
        # Definitive failure: cannot complete without workflow_id
        manager.fail_oauth_callback(state, owner_id=current_user_id, reason="Missing workflow_id in checkpoint metadata")
        return {
            "success": False,
            "status": "failed",
            "checkpoint_id": checkpoint_id,
            "error": "missing_workflow_id",
        }

    try:
        # Call the atomic completion RPC via the connector
        connector = _get_user_scoped_pinterest_connector(
            client=client,
            owner_id_uuid=current_user_id,
        )
        completion_result = connector.complete_onboarding_oauth(
            workflow_id=workflow_id,
            oauth_state=state,
            owner_id=current_user_id,
            access_token=access_token,
            refresh_token=refresh_token,
            token_type=token_type,
            expires_in=expires_in,
            external_account_id=token_data.get("account_id"),
            display_name=token_data.get("username"),
            scopes=token_scopes,
        )
    except Exception:  # pragma: no cover - completion DB failure
        # Successful token exchange but completion DB/process failure:
        # KEEP processing. Do NOT reopen to awaiting_human.
        manager.reconcile_oauth_callback(state, owner_id=current_user_id)
        return {
            "success": False,
            "status": "processing",
            "checkpoint_id": checkpoint_id,
            "error": "completion_failed",
            "error_description": "Token exchange succeeded but durable completion failed; reconciliation required",
        }

    if not completion_result.get("success"):
        # Completion failed but token exchange succeeded
        manager.reconcile_oauth_callback(state, owner_id=current_user_id)
        return {
            "success": False,
            "status": "processing",
            "checkpoint_id": checkpoint_id,
            "error": completion_result.get("error", "completion_failed"),
            "error_description": completion_result.get("error_description", "Durable completion failed"),
        }

    return {
        "success": True,
        "status": "connected",
        "checkpoint_id": checkpoint_id,
        "workflow_id": workflow_id,
        "platform": "pinterest",
        "connected": True,
        "message": "Pinterest connection established successfully",
        "next_step": completion_result.get("next_step", "complete"),
    }