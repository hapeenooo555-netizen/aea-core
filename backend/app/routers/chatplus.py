from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.chatplus import ChatPlusService

router = APIRouter(prefix="/chatplus", tags=["chatplus"])
HTML_PATH = Path(__file__).resolve().parents[1] / "static" / "chatplus.html"


class ChatMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str = Field(default="default", min_length=1, max_length=128)
    client_message_id: str | None = Field(default=None, max_length=128)

    model_config = ConfigDict(extra="forbid")


class SelectedMissionRequest(BaseModel):
    mission_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(default="default", min_length=1, max_length=128)

    model_config = ConfigDict(extra="forbid")


class ChatRejectRequest(BaseModel):
    reason: str = Field(default="", max_length=1000)

    model_config = ConfigDict(extra="forbid")


def _service(owner_id: str, client: Any | None) -> ChatPlusService:
    return ChatPlusService(owner_id, client=client)


def _raise(result: dict[str, Any], *, not_found: bool = False) -> None:
    if result.get("success"):
        return
    error = str(result.get("error") or "Chat operation failed")
    if not_found or "not found" in error.lower():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=error)
    raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=error)


@router.get("/config", include_in_schema=False)
def chat_config() -> dict[str, Any]:
    supabase_url = os.getenv("CHATPLUS_SUPABASE_URL") or os.getenv("SUPABASE_URL") or ""
    supabase_anon_key = (
        os.getenv("CHATPLUS_SUPABASE_ANON_KEY")
        or os.getenv("CHATPLUS_PUBLIC_ANON_KEY")
        or os.getenv("SUPABASE_ANON_KEY")
        or ""
    )
    return {
        "supabaseUrl": supabase_url,
        "supabaseAnonKey": supabase_anon_key,
        "authEnabled": bool(supabase_url and supabase_anon_key),
    }


@router.get("", include_in_schema=False)
def chat_index() -> FileResponse:
    return FileResponse(HTML_PATH)


@router.get("/", include_in_schema=False)
def chat_index_slash() -> FileResponse:
    return FileResponse(HTML_PATH)


@router.get("/history")
def chat_history(
    conversation_id: str = Query(default="default", min_length=1, max_length=128),
    limit: int = Query(default=100, ge=1, le=100),
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    result = _service(owner_id, client).list_messages(conversation_id, limit=limit)
    _raise(result)
    return result


@router.post("/messages")
def chat_message(
    body: ChatMessageRequest,
    background_tasks: BackgroundTasks,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    service = _service(owner_id, client)
    result = service.send_message(
        body.message,
        conversation_id=body.conversation_id,
        client_message_id=body.client_message_id,
        defer_execution=True,
    )
    _raise(result)
    mission_id = result.pop("background_mission_id", None)
    if mission_id:
        background_tasks.add_task(
            service.execute_mission,
            mission_id,
            body.message,
            body.conversation_id,
        )
    return result


@router.post("/execute-selected")
def chat_execute_selected(
    body: SelectedMissionRequest,
    background_tasks: BackgroundTasks,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Queue an existing owner-scoped mission without creating a duplicate."""
    service = _service(owner_id, client)
    mission = service.orchestration.get_mission(body.mission_id)
    if not mission:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Mission not found")
    mission_status = str(mission.get("status") or "").lower()
    if mission_status not in {"pending", "scheduled", "retrying"}:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Mission is not in an executable state")
    readiness = service.orchestration.readiness(mission)
    if not readiness.get("ready"):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=readiness)
    goal = str(mission.get("objective") or mission.get("description") or mission.get("title") or "Selected mission")
    background_tasks.add_task(service.execute_mission, body.mission_id, goal, body.conversation_id)
    return {
        "success": True,
        "conversation_id": body.conversation_id,
        "mission": mission,
        "execution": {"status": "QUEUED"},
        "state": {"state": "working", "label": "Working", "detail": "Selected mission queued for execution"},
    }


@router.get("/state")
def chat_state(
    conversation_id: str = Query(default="default", min_length=1, max_length=128),
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    result = _service(owner_id, client).current_state(conversation_id)
    _raise(result)
    return result


@router.post("/approvals/{approval_id}/approve")
def chat_approve(
    approval_id: str,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    result = _service(owner_id, client).approve_approval(approval_id)
    _raise(result, not_found=True)
    return result


@router.post("/approvals/{approval_id}/reject")
def chat_reject(
    approval_id: str,
    body: ChatRejectRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    result = _service(owner_id, client).reject_approval(approval_id, reason=body.reason)
    _raise(result, not_found=True)
    return result


__all__ = ["router"]
