"""Runway-backed video generation jobs for authenticated AEA users.

The API key remains server-side. Generation is asynchronous: clients create a job,
then poll its status. Publishing is deliberately out of scope.
"""
from __future__ import annotations

import os
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client

router = APIRouter(prefix="/video/generations", tags=["video-generation"])
RUNWAY_BASE_URL = "https://api.dev.runwayml.com/v1"
RUNWAY_VERSION = "2024-11-06"


class VideoGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    duration_seconds: int = Field(default=5, ge=2, le=10)
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    model: Literal["gen4.5", "gen4_turbo"] = "gen4.5"
    model_config = ConfigDict(extra="forbid")


def _db(client: Any) -> Any:
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is not configured.")
    return client


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    return data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])


def _headers() -> dict[str, str]:
    key = os.getenv("RUNWAYML_API_SECRET", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="Video generation is not configured. Add RUNWAYML_API_SECRET to the AEA Core environment.")
    return {
        "Authorization": f"Bearer {key}",
        "X-Runway-Version": RUNWAY_VERSION,
        "Content-Type": "application/json",
    }


def _ratio(value: str) -> str:
    return "720:1280" if value == "9:16" else "1280:720"


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_video_generation(
    body: VideoGenerationRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    headers = _headers()
    payload = {
        "model": body.model,
        "promptText": body.prompt.strip(),
        "ratio": _ratio(body.aspect_ratio),
        "duration": body.duration_seconds,
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as http:
            response = await http.post(f"{RUNWAY_BASE_URL}/text_to_video", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach the video generation provider.") from exc
    if response.status_code not in (200, 201):
        # Do not return provider headers, credentials, or raw request details.
        raise HTTPException(status_code=502, detail=f"Video provider rejected the request (HTTP {response.status_code}). Check model access and API account status.")
    try:
        provider_data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Video provider returned an invalid response.") from exc
    task_id = provider_data.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise HTTPException(status_code=502, detail="Video provider did not return a task ID.")

    job_id = str(uuid4())
    row = {
        "id": job_id,
        "owner_id": owner_id,
        "provider": "runway",
        "provider_task_id": task_id,
        "model": body.model,
        "prompt": body.prompt.strip(),
        "duration_seconds": body.duration_seconds,
        "aspect_ratio": body.aspect_ratio,
        "status": "pending",
        "provider_response": provider_data,
    }
    try:
        saved = db.table("video_generation_jobs").insert(row).execute()
    except Exception as exc:
        # The upstream task exists; report its ID so it can be reconciled rather
        # than pretending that no generation was started.
        raise HTTPException(status_code=502, detail="Provider started the video task, but AEA could not save its job record. Contact support with the provider task ID.") from exc
    if not _rows(saved):
        raise HTTPException(status_code=502, detail="AEA could not confirm that the video job was saved.")
    return {
        "success": True,
        "job": {"id": job_id, "status": "pending", "provider": "runway", "model": body.model},
        "published": False,
        "notice": "Video generation started. Poll the job endpoint for progress.",
    }


@router.get("/{job_id}")
async def get_video_generation(
    job_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    found = db.table("video_generation_jobs").select(
        "id,owner_id,provider_task_id,model,prompt,duration_seconds,aspect_ratio,status,video_url,error,created_at,updated_at"
    ).eq("id", str(job_id)).eq("owner_id", owner_id).limit(1).execute()
    rows = _rows(found)
    if not rows:
        raise HTTPException(status_code=404, detail="Video generation job not found.")
    job = rows[0]
    headers = _headers()
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            response = await http.get(f"{RUNWAY_BASE_URL}/tasks/{job['provider_task_id']}", headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not retrieve video generation status.") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail=f"Video provider status check failed (HTTP {response.status_code}).")
    try:
        task = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Video provider returned an invalid task status.") from exc

    provider_status = str(task.get("status", "pending")).lower()
    normalized = {
        "SUCCEEDED": "completed",
        "COMPLETED": "completed",
        "FAILED": "failed",
        "CANCELED": "cancelled",
        "CANCELLED": "cancelled",
        "RUNNING": "in_progress",
        "PENDING": "pending",
        "THROTTLED": "pending",
    }.get(provider_status.upper(), "in_progress")
    output = task.get("output")
    video_url = output[0] if normalized == "completed" and isinstance(output, list) and output and isinstance(output[0], str) else None
    error = task.get("failure") or task.get("error")
    update = {"status": normalized, "provider_response": task}
    if video_url:
        update["video_url"] = video_url
    if error:
        update["error"] = str(error)[:1000]
    try:
        db.table("video_generation_jobs").update(update).eq("id", str(job_id)).eq("owner_id", owner_id).execute()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AEA could not save the latest video task status.") from exc
    return {
        "success": True,
        "job": {
            "id": str(job_id),
            "status": normalized,
            "model": job["model"],
            "video_url": video_url or job.get("video_url"),
            "error": str(error)[:1000] if error else job.get("error"),
        },
        "published": False,
    }


__all__ = ["router"]
