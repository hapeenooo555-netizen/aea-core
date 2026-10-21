"""Authenticated ChatPlus endpoints for live, read-only Pinterest verification."""
from __future__ import annotations

from typing import Any
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import get_current_user_id
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
