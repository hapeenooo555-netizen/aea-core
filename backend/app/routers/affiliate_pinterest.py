"""Authenticated AEA Hub endpoints for live, read-only Pinterest verification."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.approval_gateway import ApprovalGateway
from app.services.mission_orchestration import MissionOrchestrationService
from app.services.pinterest_live_verification import PinterestVerificationError, verify_account_and_boards
from app.services.pinterest_token_vault import get_access_token

router = APIRouter(prefix="/affiliate/pinterest", tags=["affiliate-pinterest"])


class PinterestDraftPreviewRequest(BaseModel):
    """Proposed Pin content for preview only; this schema cannot publish."""

    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=800)
    destination_url: str = Field(min_length=8, max_length=2048)
    image_url: str = Field(min_length=8, max_length=2048)
    board_id: str = Field(min_length=1, max_length=128)


class PinterestDraftApprovalRequest(PinterestDraftPreviewRequest):
    """Draft plus the optional AI employee and mission the user selected in the Hub."""

    worker_id: str | None = Field(default=None, max_length=128)
    mission_id: str | None = Field(default=None, max_length=128)


def _owned_row(client: Any, table: str, row_id: str, owner_id: str) -> dict[str, Any] | None:
    """Return the row only when it belongs to the authenticated owner."""
    rows = client.table(table).select("*").eq("id", row_id).eq("owner_id", owner_id).limit(1).execute().data or []
    return rows[0] if rows else None


def _validate_http_url(value: str, field_name: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(status_code=422, detail=f"{field_name} must be a valid HTTP(S) URL")
    return value


@router.get("/verification")
def pinterest_verification(owner_id: str = Depends(get_current_user_id)) -> dict[str, Any]:
    """Verify the current user's stored Pinterest token and return account/boards.

    This endpoint is read-only. It never publishes Pins and never returns tokens.
    """
    try:
        stored = get_access_token(owner_id=owner_id)
        if not stored:
            raise HTTPException(status_code=404, detail="Pinterest is not connected for this account")
        if stored.get("expired"):
            raise HTTPException(status_code=409, detail="Pinterest access token has expired; reconnect Pinterest")
        result = verify_account_and_boards(stored["access_token"])
        # Safe OAuth evidence: expose granted scopes and timestamps, never tokens.
        return {
            "success": True,
            "account": result["account"],
            "boards": result["boards"],
            "oauth": {
                "provider": "Pinterest",
                "status": "authorized",
                "scopes": stored.get("scopes") or [],
                "authorized_at": stored.get("updated_at"),
                "expires_at": stored.get("expires_at"),
                "token_stored_server_side": True,
            },
            "published": False,
        }
    except HTTPException:
        raise
    except PinterestVerificationError as exc:
        message = str(exc)
        code = status.HTTP_409_CONFLICT if "reconnect" in message.lower() else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=code, detail=message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Pinterest credential vault is unavailable") from exc


@router.delete("/connection")
def disconnect_pinterest(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Remove the authenticated owner's encrypted credentials and connection metadata."""
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database connection is unavailable")
    try:
        from app.services.pinterest_token_vault import delete_tokens
        credentials_removed = delete_tokens(owner_id=owner_id)
        # Keep the worker runtime's connection status consistent with the vault.
        metadata_result = (
            client.table("platform_connections")
            .delete()
            .eq("owner_id", owner_id)
            .eq("platform", "pinterest")
            .execute()
        )
        metadata_removed = bool(metadata_result.data)
        return {
            "success": True,
            "disconnected": True,
            "credentials_removed": credentials_removed,
            "connection_metadata_removed": metadata_removed,
            "message": "AEA removed its stored Pinterest authorization and connection record. Also revoke AEA from Pinterest connected-app settings to revoke the provider grant.",
        }
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Pinterest authorization could not be fully removed") from exc



@router.post("/draft-preview")
def pinterest_draft_preview(
    request: PinterestDraftPreviewRequest,
    owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Validate a Pin draft against this user's live Pinterest account and boards.

    This endpoint is intentionally read-only: it never calls a create-Pin API.
    """
    _validate_http_url(request.destination_url, "destination_url")
    _validate_http_url(request.image_url, "image_url")
    try:
        stored = get_access_token(owner_id=owner_id)
        if not stored:
            raise HTTPException(status_code=404, detail="Pinterest is not connected for this account")
        if stored.get("expired"):
            raise HTTPException(status_code=409, detail="Pinterest access token has expired; reconnect Pinterest")
        verified = verify_account_and_boards(stored["access_token"])
        selected = next((board for board in verified["boards"] if board.get("id") == request.board_id), None)
        if selected is None:
            raise HTTPException(
                status_code=422,
                detail="Selected board ID does not belong to this connected Pinterest account. Refresh boards and select a valid board.",
            )
        return {
            "success": True,
            "preview": {
                "title": request.title.strip(),
                "description": request.description.strip(),
                "destination_url": request.destination_url,
                "image_url": request.image_url,
                "board": {"id": selected["id"], "name": selected["name"]},
                "account": verified["account"],
            },
            "approval_status": "pending",
            "requires_explicit_approval": True,
            "published": False,
            "message": "Preview validated against live Pinterest account and board data. Nothing was published.",
        }
    except HTTPException:
        raise
    except PinterestVerificationError as exc:
        message = str(exc)
        code = status.HTTP_409_CONFLICT if "reconnect" in message.lower() else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=code, detail=message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Pinterest credential vault is unavailable") from exc



@router.post("/draft-approval", status_code=status.HTTP_201_CREATED)
def pinterest_draft_approval(
    request: PinterestDraftApprovalRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Create a durable, owner-scoped approval request for a validated Pin draft.

    This endpoint deliberately stops at pending approval. It never publishes.
    """
    _validate_http_url(request.destination_url, "destination_url")
    _validate_http_url(request.image_url, "image_url")
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database connection is unavailable")
    try:
        stored = get_access_token(owner_id=owner_id)
        if not stored:
            raise HTTPException(status_code=404, detail="Pinterest is not connected for this account")
        if stored.get("expired"):
            raise HTTPException(status_code=409, detail="Pinterest access token has expired; reconnect Pinterest")

        verified = verify_account_and_boards(stored["access_token"])
        selected = next((board for board in verified["boards"] if board.get("id") == request.board_id), None)
        if selected is None:
            raise HTTPException(
                status_code=422,
                detail="Selected board ID does not belong to this connected Pinterest account. Refresh boards and select a valid board.",
            )

        # Link the Hub-selected mission/worker only after proving ownership.
        selected_worker_id: str | None = None
        selected_mission_id: str | None = None
        source_mission: dict[str, Any] | None = None
        if request.mission_id:
            source_mission = _owned_row(client, "missions", request.mission_id, owner_id)
            if not source_mission:
                raise HTTPException(status_code=404, detail="Selected mission was not found for this account")
            selected_mission_id = request.mission_id

        # Hub currently passes the mission ID; infer its assigned worker server-side.
        requested_worker_id = request.worker_id
        if not requested_worker_id and source_mission:
            requested_worker_id = (
                source_mission.get("assigned_worker")
                or source_mission.get("worker_id")
                or source_mission.get("assigned_worker_id")
            )

        # The Hub may omit worker_id when creating a Pin draft. Resolve the
        # sole active affiliate/Pinterest employee for this authenticated owner;
        # never guess when multiple eligible employees exist.
        if not requested_worker_id:
            active_workers = (
                client.table("workers")
                .select("*")
                .eq("owner_id", owner_id)
                .eq("status", "active")
                .limit(20)
                .execute()
                .data or []
            )
            eligible_workers = [
                worker for worker in active_workers
                if any(
                    term in str(worker.get("role") or "").lower()
                    for term in ("affiliate", "pinterest")
                )
            ]
            if len(eligible_workers) == 1:
                requested_worker_id = str(eligible_workers[0]["id"])
            elif len(eligible_workers) > 1:
                raise HTTPException(
                    status_code=409,
                    detail="More than one active affiliate/Pinterest employee is available. Select the employee for this Pin.",
                )
            else:
                raise HTTPException(
                    status_code=422,
                    detail="No active affiliate/Pinterest employee is assigned to this account. Activate an employee before creating a Pin approval.",
                )

        selected_worker = _owned_row(client, "workers", requested_worker_id, owner_id)
        if not selected_worker or str(selected_worker.get("status") or "").lower() != "active":
            raise HTTPException(status_code=404, detail="Selected active worker was not found for this account")
        selected_worker_id = requested_worker_id

        if source_mission and selected_worker_id:
            mission_worker_id = (
                source_mission.get("worker_id")
                or source_mission.get("assigned_worker")
                or source_mission.get("assigned_worker_id")
            )
            if mission_worker_id and mission_worker_id != selected_worker_id:
                raise HTTPException(status_code=422, detail="Selected mission is not assigned to the selected worker")

        title = request.title.strip()
        description = request.description.strip()
        destination_url = request.destination_url.strip()
        image_url = request.image_url.strip()
        # The idempotency key ties repeated submissions of the same draft to
        # one mission, preventing accidental duplicate approval requests.
        import hashlib
        fingerprint = hashlib.sha256(
            "|".join([owner_id, title, description, destination_url, image_url, selected["id"]]).encode("utf-8")
        ).hexdigest()[:32]
        orchestration = MissionOrchestrationService(owner_id, client=client)
        mission_result = orchestration.create_mission(
            objective=f"Review and approve Pinterest Pin: {title}",
            title=f"Pinterest approval: {title[:90]}",
            metadata={
                "source": "affiliate_pinterest_draft",
                "platform": "pinterest",
                "board_id": selected["id"],
                "source_worker_id": selected_worker_id,
                "source_mission_id": selected_mission_id,
            },
            idempotency_key=f"pinterest-draft-approval:{fingerprint}",
        )
        if not mission_result.get("success") or not (mission_result.get("mission") or {}).get("id"):
            raise HTTPException(status_code=503, detail="Could not create a durable mission for this Pin approval")
        mission_id = mission_result["mission"]["id"]

        # The approval mission is a new durable mission, not the selected
        # source mission. Persist the validated owner-scoped worker assignment
        # on the mission row so the runtime can resolve its employee.
        if selected_worker_id:
            try:
                assigned = (
                    client.table("missions")
                     .update({"assigned_worker": selected_worker_id})
                    .eq("id", mission_id)
                    .eq("owner_id", owner_id)
                    .execute()
                )
                rows = assigned.data or []
                if not rows or str(rows[0].get("assigned_worker") or "") != selected_worker_id:
                    raise HTTPException(
                        status_code=503,
                        detail="Pinterest approval mission was created, but its worker assignment could not be verified",
                    )
            except HTTPException:
                raise
            except Exception as exc:
                raise HTTPException(
                    status_code=503,
                    detail="Pinterest approval mission assigned_worker persistence failed",
                ) from exc

        gateway = ApprovalGateway(client=client)
        existing = gateway.list_requests(mission_id=mission_id, status="pending", limit=20, client=client)
        for approval in existing:
            if approval.get("owner_id") == owner_id and approval.get("action_type") == "publish_content":
                payload = approval.get("payload") or {}
                if payload.get("source") == "affiliate_pinterest_draft":
                    return {
                        "success": True,
                        "approval": approval,
                        "approval_request_id": approval.get("id"),
                        "mission_id": mission_id,
                        "preview": payload.get("preview"),
                        "published": False,
                        "idempotent": True,
                        "message": "This draft already has a pending approval. Nothing was published.",
                    }

        preview = {
            "title": title,
            "description": description,
            "destination_url": destination_url,
            "image_url": image_url,
            "board": {"id": selected["id"], "name": selected["name"]},
            "account": verified["account"],
        }
        result = gateway.create_request(
            mission_id=mission_id,
            action_type="publish_content",
            risk_level="WRITE_EXTERNAL",
            owner_id=owner_id,
            client=client,
            payload={
                "platform": "pinterest",
                "worker_id": selected_worker_id,
                "content": {
                    "title": title,
                    "pin_text": description,
                    "link_url": destination_url,
                    "image_url": image_url,
                    "board_id": selected["id"],
                    "board_name": selected["name"],
                },
                "source": "affiliate_pinterest_draft",
                "source_worker_id": selected_worker_id,
                "source_mission_id": selected_mission_id,
                "preview": preview,
            },
        )
        if not result.get("success") or not result.get("request"):
            raise HTTPException(status_code=503, detail="Could not persist the Pinterest approval request")
        return {
            "success": True,
            "approval": result["request"],
            "approval_request_id": result["request"].get("id"),
            "mission_id": mission_id,
            "source_mission_id": selected_mission_id,
            "preview": preview,
            "published": False,
            "requires_explicit_approval": True,
            "message": "Pin draft validated and approval request saved. It will not publish until you explicitly approve it.",
        }
    except HTTPException:
        raise
    except PinterestVerificationError as exc:
        message = str(exc)
        code = status.HTTP_409_CONFLICT if "reconnect" in message.lower() else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=code, detail=message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Could not create the Pinterest approval request") from exc
