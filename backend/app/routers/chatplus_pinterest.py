"""Authenticated ChatPlus endpoints for live, read-only Pinterest verification."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.dependencies import get_current_user_id
from app.services.pinterest_live_verification import PinterestVerificationError, verify_account_and_boards
from app.services.pinterest_token_vault import get_access_token

router = APIRouter(prefix="/chatplus/pinterest", tags=["chatplus"])


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
