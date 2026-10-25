"""Authenticated ChatPlus endpoints for live, read-only Pinterest verification."""
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

router = APIRouter(prefix="/chatplus/pinterest", tags=["chatplus"])


class PinterestDraftPreviewRequest(BaseModel):
    """Proposed Pin content for preview only; this schema cannot publish."""

    title: str = Field(min_length=1, max_length=100)
    description: str = Field(min_length=1, max_length=800)
    destination_url: str = Field(min_length=8, max_length=2048)
    image_url: str = Field(min_length=8, max_length=2048)
    board_id: str = Field(min_length=1, max_length=128)


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
        return {"success": True, "account": result["account"], "boards": result["boards"], "published": False}
    except HTTPException:
        raise
    except PinterestVerificationError as exc:
        message = str(exc)
        code = status.HTTP_409_CONFLICT if "reconnect" in message.lower() else status.HTTP_502_BAD_GATEWAY
        raise HTTPException(status_code=code, detail=message) from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail="Pinterest credential vault is unavailable") from exc



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
    request: PinterestDraftPreviewRequest,
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
            metadata={"source": "chatplus_pinterest_draft", "platform": "pinterest", "board_id": selected["id"]},
            idempotency_key=f"pinterest-draft-approval:{fingerprint}",
        )
        if not mission_result.get("success") or not (mission_result.get("mission") or {}).get("id"):
            raise HTTPException(status_code=503, detail="Could not create a durable mission for this Pin approval")
        mission_id = mission_result["mission"]["id"]

        gateway = ApprovalGateway(client=client)
        existing = gateway.list_requests(mission_id=mission_id, status="pending", limit=20, client=client)
        for approval in existing:
            if approval.get("owner_id") == owner_id and approval.get("action_type") == "publish_content":
                payload = approval.get("payload") or {}
                if payload.get("source") == "chatplus_pinterest_draft":
                    return {
                        "success": True,
                        "approval": approval,
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
                "worker_id": owner_id,
                "content": {
                    "title": title,
                    "pin_text": description,
                    "link_url": destination_url,
                    "image_url": image_url,
                    "board_id": selected["id"],
                    "board_name": selected["name"],
                },
                "source": "chatplus_pinterest_draft",
                "preview": preview,
            },
        )
        if not result.get("success") or not result.get("request"):
            raise HTTPException(status_code=503, detail="Could not persist the Pinterest approval request")
        return {
            "success": True,
            "approval": result["request"],
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
