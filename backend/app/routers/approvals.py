"""Approval API routes for Sprint 7.2-A + P1-1 resume pipeline."""

from __future__ import annotations

from typing import Any
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict

from app.dependencies import get_current_user_id, get_user_scoped_client, verify_ownership
from app import database as database_module
from app.services.approval_gateway import ApprovalGateway
from app.services import meta_user_connection as meta_vault
from app.services.approval_resume_service import ApprovalResumeService
from app.services.employee_vertical_slice import EmployeeVerticalSlice
from app.services.connectors.pinterest_connector import PinterestConnector
from app.services.connectors.meta_marketing import MetaMarketingConnector
from app.services.connectors.registry import ConnectorRegistry
from app.services.human_intervention import HumanInterventionManager
from app.services.mission_execution_service import MissionExecutionService
from app.services.mission_orchestration import MissionOrchestrationService
from app.services.stores.onboarding_workflow_store import OnboardingWorkflowStore
from app.services.stores.pin_publish_store import PinPublishStore
from app.services.stores.platform_connection_store import PlatformConnectionStore

router = APIRouter(prefix="/approvals", tags=["approvals"])


def _build_resume_service(current_user_id: str | None = None) -> ApprovalResumeService:
    """Construct an ``ApprovalResumeService`` with the canonical registry."""

    registry = ConnectorRegistry()
    registry.register(PinterestConnector(workflow_store=OnboardingWorkflowStore(durable_required=True)))
    registry.register(MetaMarketingConnector())
    return ApprovalResumeService(
        approval_gateway=ApprovalGateway(),
        connector_registry=registry,
        human_intervention_manager=HumanInterventionManager(),
    )


resume_service = _build_resume_service()


class MetaPublishApprovalRequest(BaseModel):
    """Owner-scoped Facebook/Instagram publishing request from My Store."""
    worker_id: str
    channel: str
    message: str
    image_url: str | None = None
    page_id: str | None = None
    instagram_account_id: str | None = None

    model_config = ConfigDict(extra="forbid")


class ApprovalApproveRequest(BaseModel):
    """Request payload for approving an approval request."""

    approved_by: str | None = None

    model_config = ConfigDict(extra="allow")


class ApprovalRejectRequest(BaseModel):
    """Request payload for rejecting an approval request."""

    reason: str | None = None
    rejected_by: str | None = None

    model_config = ConfigDict(extra="allow")


class ApprovalListFilters(BaseModel):
    """Query parameters for filtering approvals."""

    mission_id: str | None = None
    status: str | None = None
    limit: int = 100

    model_config = ConfigDict(extra="allow")


def _get_approval_gateway(client: Any | None = None) -> ApprovalGateway:
    """Get an ApprovalGateway instance with optional scoped client."""
    return ApprovalGateway(client=client)


def _get_resume_service(current_user_id: str, client: Any | None = None) -> ApprovalResumeService:
    """Get an ApprovalResumeService scoped to the current user."""
    registry = ConnectorRegistry()
    registry.register(MetaMarketingConnector())
    registry.register(
        PinterestConnector(
            workflow_store=OnboardingWorkflowStore(
                client=client,
                durable_required=True,
            ),
            connection_store=PlatformConnectionStore(client=client),
            pin_store=PinPublishStore(
                client=database_module.get_supabase_pin_publish_client(),
                durable_required=True,
            ),
        ),
    )
    return ApprovalResumeService(
        approval_gateway=ApprovalGateway(client=client),
        connector_registry=registry,
        human_intervention_manager=HumanInterventionManager(client=client),
        mission_client=client,
    )


def _resume_approval_for_owner(
    approval_id: str,
    approval: dict[str, Any],
    owner_id: str,
    client: Any | None,
) -> dict[str, Any]:
    """Resume P1-8 Pinterest approvals through the durable employee execution.

    Legacy/non-Pinterest approvals retain the existing resume service. P1-8
    Pinterest approvals must also complete or pause their MissionExecution
    record and approval step, not only update the parent mission.
    """
    payload = approval.get("payload") if isinstance(approval.get("payload"), dict) else {}
    if (
        approval.get("action_type") in {"start_platform_onboarding", "publish_content"}
        and str(payload.get("platform") or "").lower() == "pinterest"
    ):
        return EmployeeVerticalSlice(owner_id, client=client).resume_approval(approval_id)
    return _get_resume_service(owner_id, client).resume(
        approval_id,
        current_user_id=owner_id,
    )


def _update_p1_8_mission_state(
    approval: dict[str, Any] | None,
    outcome: dict[str, Any],
    owner_id: str,
    client: Any | None,
) -> None:
    """Reflect an approval outcome on its owned P1-8 mission, when applicable."""

    mission_id = (approval or {}).get("mission_id")
    if not mission_id:
        return

    orchestration = MissionOrchestrationService(owner_id, client=client)
    mission = orchestration.get_mission(mission_id)
    # Strongest existing P1-8 marker: ``orchestration_idempotency_key``
    # is an orchestration-specific column set exclusively by
    # MissionOrchestrationService.create_mission(). Its key *presence*
    # in the returned row distinguishes in-memory P1-8 records from
    # legacy records that lack the key entirely. When using a DB
    # client (SELECT *), every row has the column, so we additionally
    # require a non-null ``objective`` — also always populated by
    # P1-8 — to reject legacy rows.
    if not mission or "orchestration_idempotency_key" not in mission or not mission.get("objective"):
        return

    status_text = str(outcome.get("status") or "").lower()
    target = {
        "completed": "completed",
        "awaiting_human_intervention": "waiting_human",
        "approval_rejected": "failed",
        "approval_expired": "cancelled",
        "resume_failed": "failed",
        "publish_uncertain": "waiting_human",
    }.get(status_text)
    # A first publish resumes with "resumed"; it is only complete once the
    # connector confirms a real Pin ID.
    result = outcome.get("result") if isinstance(outcome.get("result"), dict) else {}
    if status_text == "resumed" and (outcome.get("pin_id") or result.get("pin_id")):
        target = "completed"
    if result.get("status") == "publish_uncertain":
        target = "waiting_human"
    if target:
        orchestration.transition(mission_id, target, result=outcome)


@router.post("/meta-publish-request", status_code=status.HTTP_201_CREATED)
async def create_meta_publish_request(
    request: MetaPublishApprovalRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Create an owner-scoped, approval-gated Meta publish request; never publish here."""
    channel = request.channel.strip().lower()
    if channel not in {"facebook", "instagram"}:
        raise HTTPException(status_code=422, detail="Meta channel must be facebook or instagram.")
    message = request.message.strip()
    if not message or len(message) > 5000:
        raise HTTPException(status_code=422, detail="A message of 1–5000 characters is required.")
    if request.image_url and (not request.image_url.startswith("https://") or len(request.image_url) > 4096):
        raise HTTPException(status_code=422, detail="Selected media must use a valid HTTPS URL.")
    if channel == "instagram" and not request.image_url:
        raise HTTPException(status_code=422, detail="Instagram publishing requires a selected image asset.")
    if channel == "facebook" and not request.page_id:
        raise HTTPException(status_code=422, detail="Select a connected Facebook Page.")
    if channel == "instagram" and not request.instagram_account_id:
        raise HTTPException(status_code=422, detail="Select a connected Instagram professional account.")
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database connection is unavailable.")

    worker_rows = client.table("workers").select("id,status").eq("id", request.worker_id).eq("owner_id", current_user_id).limit(1).execute().data or []
    if not worker_rows or str(worker_rows[0].get("status") or "").lower() != "active":
        raise HTTPException(status_code=404, detail="Selected active AEA worker was not found for this account.")

    connection = meta_vault.connection_status(current_user_id)
    assets = connection.get("assets") if isinstance(connection, dict) else {}
    assets = assets if isinstance(assets, dict) else {}
    if channel == "facebook":
        pages = assets.get("facebook_pages") if isinstance(assets.get("facebook_pages"), list) else []
        if not any(isinstance(item, dict) and str(item.get("id")) == request.page_id for item in pages):
            raise HTTPException(status_code=422, detail="Selected Facebook Page is not connected to this account.")
    else:
        accounts = assets.get("instagram_accounts") if isinstance(assets.get("instagram_accounts"), list) else []
        if not any(isinstance(item, dict) and str(item.get("id")) == request.instagram_account_id for item in accounts):
            raise HTTPException(status_code=422, detail="Selected Instagram account is not connected to this account.")

    import hashlib
    fingerprint = hashlib.sha256("|".join([
        current_user_id, channel, request.worker_id, request.page_id or "",
        request.instagram_account_id or "", request.image_url or "", message,
    ]).encode("utf-8")).hexdigest()[:32]
    orchestration = MissionOrchestrationService(current_user_id, client=client)
    mission_result = orchestration.create_mission(
        objective=f"Review and approve {channel.title()} post from My Store",
        title=f"{channel.title()} publishing approval",
        metadata={"source": "my_store_distribution", "platform": "meta", "channel": channel},
        idempotency_key=f"meta-my-store-publish:{fingerprint}",
    )
    mission = mission_result.get("mission") if mission_result.get("success") else None
    mission_id = mission.get("id") if isinstance(mission, dict) else None
    if not mission_id:
        raise HTTPException(status_code=503, detail="Could not create a durable Meta publishing mission.")
    assigned = client.table("missions").update({"assigned_worker": request.worker_id}).eq("id", mission_id).eq("owner_id", current_user_id).execute().data or []
    if not assigned or str(assigned[0].get("assigned_worker") or "") != request.worker_id:
        raise HTTPException(status_code=503, detail="Could not verify the Meta approval worker assignment.")

    gateway = ApprovalGateway(client=client)
    existing = gateway.list_requests(mission_id=mission_id, status="pending", limit=10, client=client)
    for approval in existing:
        payload = approval.get("payload") if isinstance(approval.get("payload"), dict) else {}
        if approval.get("owner_id") == current_user_id and approval.get("action_type") == "publish_content" and payload.get("platform") == "meta":
            return {"success": True, "approval": approval, "approval_request_id": approval.get("id"), "mission_id": mission_id, "published": False, "idempotent": True}

    created = gateway.create_request(
        mission_id=mission_id,
        action_type="publish_content",
        risk_level="WRITE_EXTERNAL",
        owner_id=current_user_id,
        client=client,
        payload={
            "platform": "meta",
            "worker_id": request.worker_id,
            "content": {
                "channel": channel,
                "message": message,
                **({"image_url": request.image_url} if request.image_url else {}),
                **({"page_id": request.page_id} if request.page_id else {}),
                **({"instagram_account_id": request.instagram_account_id} if request.instagram_account_id else {}),
            },
        },
    )
    if not created.get("success") or not created.get("request"):
        raise HTTPException(status_code=503, detail="Could not persist the Meta approval request.")
    return {
        "success": True,
        "approval": created["request"],
        "approval_request_id": created["request"].get("id"),
        "mission_id": mission_id,
        "published": False,
        "requires_explicit_approval": True,
        "message": "Meta publishing request saved. Nothing will publish until you explicitly approve it.",
    }


@router.get("/{approval_id}")
async def get_approval(
    approval_id: str,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Retrieve a specific approval request owned by the current user."""

    gateway = _get_approval_gateway(client)
    approval = gateway.get_request(approval_id, client=client)
    if approval is None or approval.get("owner_id") != current_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval request not found")
    return {"success": True, "approval": approval}


@router.get("")
async def list_approvals(
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
    mission_id: str | None = None,
    status_filter: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """List approval requests owned by the current user with optional filtering."""

    gateway = _get_approval_gateway(client)
    approvals = gateway.list_requests(
        mission_id=mission_id,
        status=status_filter,
        limit=limit,
        client=client,
    )
    # Filter to only current user's approvals for defense in depth.
    user_approvals = [a for a in approvals if a.get("owner_id") == current_user_id]

    # Listing approvals is also the reconciliation boundary for stale
    # requests. Expiration is persisted by the gateway's atomic transition;
    # then the associated P1-8 mission is cancelled so the dashboard cannot
    # leave it looking runnable after its approval window has closed.
    for approval in user_approvals:
        if str(approval.get("status") or "").lower() != "pending":
            continue
        if not gateway.is_expired(approval.get("id")):
            continue
        expired = gateway.get_request(approval.get("id"), client=client) or approval
        if expired.get("owner_id") == current_user_id and str(expired.get("status") or "").lower() == "expired":
            _update_p1_8_mission_state(
                expired,
                {"status": "approval_expired", "approval_request_id": expired.get("id")},
                current_user_id,
                client,
            )
            approval.update(expired)

    return {"success": True, "approvals": user_approvals, "count": len(user_approvals)}


@router.post("/{approval_id}/approve", status_code=status.HTTP_200_OK)
async def approve_approval(
    approval_id: str,
    request: ApprovalApproveRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Approve a pending approval request and trigger resume.

    The authenticated user must own the approval request.
    The ``approved_by`` field is derived from current_user_id,
    never from the request body.
    """

    gateway = _get_approval_gateway(client)
    approval = gateway.get_request(approval_id, client=client)
    if approval is None or approval.get("owner_id") != current_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval request not found")

    result = gateway.approve_request(approval_id, approved_by=current_user_id)

    if not result.get("success"):
        error = result.get("error", "Failed to approve request")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))

    approval = result.get("request")

    # A repeated approval click must not replay a connector side effect when
    # the first approval already persisted its execution outcome. If the
    # process crashed after the approval CAS but before recording execution,
    # the missing outcome deliberately leaves the operation recoverable.
    if result.get("idempotent") and isinstance(approval, dict):
        metadata = approval.get("metadata")
        if not isinstance(metadata, dict):
            # ApprovalGateway normalizes the database JSONB metadata column
            # into the public "payload" field.
            metadata = approval.get("payload")
        metadata = metadata if isinstance(metadata, dict) else {}
        prior_execution = metadata.get("execution")
        if isinstance(prior_execution, dict) and prior_execution.get("status"):
            return {
                "success": prior_execution.get("status") not in {
                    "resume_failed", "approval_not_found", "approval_unauthorized"
                },
                "approval": approval,
                "resume": prior_execution,
                "idempotent": True,
            }
        # The approval CAS has already been won by another request, but its
        # execution outcome is not persisted yet. Do not start a second
        # connector call in this race window. A recovery worker/operator can
        # reconcile an approved request with no execution record.
        return {
            "success": True,
            "approval": approval,
            "resume": {
                "status": "awaiting_resume",
                "message": "Approval is already recorded; execution outcome is not yet available.",
            },
            "idempotent": True,
        }

    resume_result = _resume_approval_for_owner(
        approval_id,
        approval,
        current_user_id,
        client,
    )
    _update_p1_8_mission_state(approval, resume_result, current_user_id, client)

    # Persist connector execution separately from human approval. An approved
    # request is not proof that Meta accepted or delivered a WhatsApp message.
    try:
        from datetime import datetime, timezone
        import logging

        existing_metadata = approval.get("metadata") if isinstance(approval, dict) else {}
        execution_metadata = dict(existing_metadata) if isinstance(existing_metadata, dict) else {}
        execution_metadata["execution"] = {
            "status": resume_result.get("status"),
            "action_type": resume_result.get("action_type"),
            "platform": resume_result.get("platform"),
            "error": resume_result.get("error"),
            "result": resume_result.get("result"),
            "recorded_at": datetime.now(timezone.utc).isoformat(),
        }
        client.table("approval_requests").update(
            {"metadata": execution_metadata}
        ).eq("id", approval_id).eq("owner_id", current_user_id).execute()
        approval["metadata"] = execution_metadata
    except Exception:
        logging.getLogger(__name__).exception(
            "Failed to persist approval execution outcome for %s", approval_id
        )

    return {
        "success": resume_result.get("status") not in {
            "resume_failed", "approval_not_found", "approval_unauthorized"
        },
        "approval": approval,
        "resume": resume_result,
    }


@router.post("/{approval_id}/reject", status_code=status.HTTP_200_OK)
async def reject_approval(
    approval_id: str,
    request: ApprovalRejectRequest,
    current_user_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Reject a pending approval request.

    The authenticated user must own the approval request.
    The ``rejected_by`` field is derived from current_user_id,
    never from the request body.
    """

    gateway = _get_approval_gateway(client)
    approval = gateway.get_request(approval_id, client=client)
    if approval is None or approval.get("owner_id") != current_user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Approval request not found")

    result = gateway.reject_request(
        approval_id,
        reason=request.reason or "",
        rejected_by=current_user_id,
    )

    if not result.get("success"):
        error = result.get("error", "Failed to reject request")
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error))

    rejected_request = result.get("request") or approval
    execution_id = (rejected_request.get("payload") or {}).get("execution_id")
    if execution_id:
        terminal = MissionExecutionService(client=client, durable_required=True).mark_failed(
            execution_id,
            current_user_id,
            error="Approval rejected",
            result={"decision": "REJECTED", "approval_request_id": approval_id},
        )
        if not terminal.get("success"):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=terminal.get("error", "Failed to persist rejected execution"),
            )

    _update_p1_8_mission_state(rejected_request, {"status": "approval_rejected"}, current_user_id, client)

    return {"success": True, "approval": result.get("request")}
