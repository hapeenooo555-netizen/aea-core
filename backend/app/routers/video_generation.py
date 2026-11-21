"""Runway-backed video generation jobs for authenticated AEA users.

The API key remains server-side. Generation is asynchronous: clients create a job,
then poll its status. Publishing is deliberately out of scope.
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from app import database as database_module
from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.llm_provider import get_llm_provider

router = APIRouter(prefix="/video/generations", tags=["video-generation"])
RUNWAY_BASE_URL = "https://api.dev.runwayml.com/v1"
RUNWAY_VERSION = "2024-11-06"


class VideoGenerationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=1000)
    duration_seconds: int = Field(default=5, ge=2, le=10)
    aspect_ratio: Literal["9:16", "16:9"] = "9:16"
    model: Literal["gen4.5"] = "gen4.5"
    model_config = ConfigDict(extra="forbid")


def _db(client: Any) -> Any:
    if client is None:
        raise HTTPException(status_code=503, detail="User-scoped database access is not configured.")
    return client


def _rows(response: Any) -> list[dict[str, Any]]:
    data = getattr(response, "data", None)
    return data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])


VIDEO_ASSET_BUCKET = "aea-video-assets"
MAX_VIDEO_ASSET_BYTES = 480 * 1024 * 1024
_ALLOWED_VIDEO_HOST_SUFFIXES = (".cloudfront.net", ".runwayml.com", ".amazonaws.com")


def _video_object_path(owner_id: str, job_id: str, project_id: str | None = None, scene_id: str | None = None) -> str:
    if project_id and scene_id:
        return f"{owner_id}/{project_id}/scenes/{scene_id}.mp4"
    return f"{owner_id}/jobs/{job_id}.mp4"


def _validate_runway_asset_url(source_url: str) -> None:
    parsed = urlsplit(source_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not hostname or not any(
        hostname.endswith(suffix) for suffix in _ALLOWED_VIDEO_HOST_SUFFIXES
    ):
        raise RuntimeError("Runway returned a video URL on an unsupported host.")
    if parsed.username or parsed.password:
        raise RuntimeError("Runway returned an invalid video URL.")


async def _persist_runway_video(
    client: Any,
    owner_id: str,
    job_id: str,
    source_url: str,
    project_id: str | None = None,
    scene_id: str | None = None,
) -> str:
    """Copy a provider's temporary MP4 into the owner's private AEA Storage namespace."""
    _validate_runway_asset_url(source_url)
    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(120.0, connect=20.0),
            follow_redirects=False,
        ) as http:
            response = await http.get(source_url)
    except httpx.HTTPError as exc:
        raise RuntimeError("Could not download the completed video from Runway.") from exc
    if response.status_code != 200:
        raise RuntimeError(f"Runway video download failed (HTTP {response.status_code}).")
    content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type not in {"video/mp4", "application/octet-stream", "binary/octet-stream"}:
        raise RuntimeError("Runway returned an unexpected video content type.")
    video_bytes = response.content
    if not video_bytes or len(video_bytes) > MAX_VIDEO_ASSET_BYTES:
        raise RuntimeError("Runway video is empty or exceeds the AEA video storage limit.")
    object_path = _video_object_path(owner_id, job_id, project_id, scene_id)
    try:
        client.storage.from_(VIDEO_ASSET_BUCKET).upload(
            object_path,
            video_bytes,
            file_options={"content-type": "video/mp4", "upsert": "true"},
        )
    except Exception as exc:
        raise RuntimeError("AEA could not persist the completed video in private storage.") from exc
    return object_path


def _create_video_signed_url(client: Any, object_path: str) -> str:
    try:
        signed = client.storage.from_(VIDEO_ASSET_BUCKET).create_signed_url(object_path, 900)
        url = signed.get("signedURL") or signed.get("signedUrl")
        if not url:
            raise RuntimeError("Storage did not return a signed URL.")
        return url
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not create a private video playback link.") from exc


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


RUNWAY_MODEL_CREDITS_PER_SECOND = {"gen4.5": 12}


async def _runway_available_credits(headers: dict[str, str]) -> int:
    """Read the Runway API organization credit balance before any billable generation."""
    try:
        async with httpx.AsyncClient(timeout=15.0) as http:
            response = await http.get(f"{RUNWAY_BASE_URL}/organization", headers=headers)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=503, detail="Could not verify Runway API credit balance; generation was not started.") from exc
    if response.status_code != 200:
        raise HTTPException(status_code=503, detail=f"Could not verify Runway API credit balance (HTTP {response.status_code}); generation was not started.")
    try:
        payload = response.json()
        available = payload.get("creditBalance")
    except (ValueError, AttributeError):
        available = None
    if isinstance(available, bool) or not isinstance(available, int) or available < 0:
        raise HTTPException(status_code=503, detail="Runway returned an invalid organization credit balance; generation was not started.")
    return available


def _estimate_runway_credits(model: str, durations: list[int]) -> int:
    rate = RUNWAY_MODEL_CREDITS_PER_SECOND.get(model)
    if rate is None:
        raise HTTPException(status_code=422, detail="Unsupported Runway model for credit estimation.")
    return rate * sum(durations)


async def _require_runway_credits(headers: dict[str, str], model: str, durations: list[int]) -> dict[str, int | bool]:
    available = await _runway_available_credits(headers)
    estimated = _estimate_runway_credits(model, durations)
    shortfall = max(0, estimated - available)
    if shortfall:
        raise HTTPException(status_code=409, detail={
            "code": "insufficient_credits",
            "message": "Insufficient Runway API credits. No generation requests were sent.",
            "available_credits": available,
            "estimated_credits": estimated,
            "shortfall_credits": shortfall,
            "model": model,
        })
    return {"available_credits": available, "estimated_credits": estimated, "can_generate": True}


def _persist_scene_failure(
    db: Any,
    owner_id: str,
    project_id: UUID,
    scene_id: str,
    diagnostic: dict[str, Any],
    http_status: int | None = None,
) -> None:
    """Persist a bounded, credential-free failure so project refresh is authoritative."""
    updated = db.table("video_project_scenes").update({
        "status": "failed",
        "provider_error": diagnostic,
        "http_status": http_status,
    }).eq("id", str(scene_id)).eq("project_id", str(project_id)).eq("owner_id", owner_id).execute()
    if not _rows(updated):
        raise RuntimeError("Scene failure diagnostic was not persisted")


@router.post("", status_code=status.HTTP_202_ACCEPTED)
async def create_video_generation(
    body: VideoGenerationRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    headers = _headers()
    await _require_runway_credits(headers, body.model, [body.duration_seconds])
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


@router.get("/provider/credits")
async def get_runway_provider_credits(
    owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Read-only connectivity check; never submits a generation request."""
    del owner_id
    headers = _headers()
    balance = await _runway_available_credits(headers)
    return {
        "success": True,
        "provider": "runway",
        "connected": True,
        "credit_balance": balance,
        "generation_started": False,
    }


@router.get("/{job_id}")
async def get_video_generation(
    job_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    found = db.table("video_generation_jobs").select(
        "id,owner_id,provider_task_id,model,prompt,duration_seconds,aspect_ratio,status,video_url,video_storage_path,error,created_at,updated_at"
    ).eq("id", str(job_id)).eq("owner_id", owner_id).limit(1).execute()
    rows = _rows(found)
    if not rows:
        raise HTTPException(status_code=404, detail="Video generation job not found.")
    job = rows[0]
    if job.get("video_storage_path"):
        return {
            "success": True,
            "job": {
                "id": str(job_id),
                "status": job.get("status"),
                "model": job["model"],
                "video_url": _create_video_signed_url(client, job["video_storage_path"]),
                "error": job.get("error"),
            },
            "published": False,
        }
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
    # Propagate each scene task's terminal/progress state into its owning project scene.
    scene_rows: list[dict[str, Any]] = []
    try:
        scene_rows = _rows(db.table("video_project_scenes").select(
            "id,project_id"
        ).eq("generation_job_id", str(job_id)).eq("owner_id", owner_id).limit(1).execute())
        if normalized == "completed":
            # Keep the provider URL only until the durable private copy is confirmed.
            existing_url = video_url or job.get("video_url")
            if not job.get("video_storage_path") and existing_url:
                scene = scene_rows[0] if scene_rows else {}
                object_path = await _persist_runway_video(
                    client,
                    owner_id,
                    str(job_id),
                    existing_url,
                    str(scene["project_id"]) if scene.get("project_id") else None,
                    str(scene["id"]) if scene.get("id") else None,
                )
                db.table("video_generation_jobs").update({
                    "video_storage_path": object_path,
                    "video_url": None,
                }).eq("id", str(job_id)).eq("owner_id", owner_id).execute()
                if scene:
                    db.table("video_project_scenes").update({
                        "video_storage_path": object_path,
                        "video_url": None,
                        "status": "completed",
                    }).eq("id", str(scene["id"])).eq("project_id", str(scene["project_id"])).eq("owner_id", owner_id).execute()
                job["video_storage_path"] = object_path
        db.table("video_project_scenes").update({
            "status": "completed" if normalized == "completed" else ("failed" if normalized in {"failed", "cancelled"} else "generating"),
            **({"video_url": video_url} if video_url and not job.get("video_storage_path") else {}),
        }).eq("generation_job_id", str(job_id)).eq("owner_id", owner_id).execute()
    except Exception as exc:
        # Keep the completed provider task recoverable; a later refresh retries storage.
        if normalized == "completed":
            raise HTTPException(status_code=502, detail="Runway completed the clip, but AEA could not save it to permanent private storage. Refresh to retry; no new generation is needed.") from exc
        raise HTTPException(status_code=502, detail="AEA could not synchronize the scene status.") from exc
    playback_url = (
        _create_video_signed_url(client, job["video_storage_path"])
        if job.get("video_storage_path")
        else (video_url or job.get("video_url"))
    )
    return {
        "success": True,
        "job": {
            "id": str(job_id),
            "status": normalized,
            "model": job["model"],
            "video_url": playback_url,
            "error": str(error)[:1000] if error else job.get("error"),
        },
        "published": False,
    }


class VideoSceneInput(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    narration: str = Field(default="", max_length=12000)
    visual_prompt: str = Field(min_length=1, max_length=1000)
    duration_seconds: int = Field(default=5, ge=2, le=10)


class VideoProjectRequest(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    format: Literal["short", "long_form", "storytelling", "documentary", "advertisement"] = "storytelling"
    target_duration_seconds: int = Field(default=300, ge=15, le=7200)
    aspect_ratio: Literal["9:16", "16:9"] = "16:9"
    visual_style: str = Field(default="cinematic_realistic", max_length=80)
    presenter_mode: Literal["faceless", "realistic_human", "mixed"] = "faceless"
    scenes: list[VideoSceneInput] = Field(min_length=1, max_length=720)
    model_config = ConfigDict(extra="forbid")


@router.post("/projects", status_code=status.HTTP_201_CREATED)
async def create_video_project(
    body: VideoProjectRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    project_id = str(uuid4())
    project = {
        "id": project_id,
        "owner_id": owner_id,
        "title": body.title,
        "format": body.format,
        "target_duration_seconds": body.target_duration_seconds,
        "aspect_ratio": body.aspect_ratio,
        "visual_style": body.visual_style,
        "presenter_mode": body.presenter_mode,
        "status": "ready",
    }
    # A project is not accepted unless both its parent and scenes are persisted.
    try:
        created = db.table("video_projects").insert(project).execute()
        if not _rows(created):
            raise RuntimeError("Project insert returned no row")
        scene_rows = [{
            "id": str(uuid4()),
            "project_id": project_id,
            "owner_id": owner_id,
            "scene_number": index,
            "title": scene.title,
            "narration": scene.narration,
            "visual_prompt": scene.visual_prompt,
            "duration_seconds": scene.duration_seconds,
            "status": "pending",
        } for index, scene in enumerate(body.scenes, start=1)]
        saved_scenes = db.table("video_project_scenes").insert(scene_rows).execute()
        if len(_rows(saved_scenes)) != len(scene_rows):
            raise RuntimeError("Scene insert count mismatch")
    except Exception as exc:
        # Best-effort rollback prevents an incomplete project from being exposed.
        try:
            db.table("video_projects").delete().eq("id", project_id).eq("owner_id", owner_id).execute()
        except Exception:
            pass
        raise HTTPException(status_code=502, detail="AEA could not persist the complete video project and its scenes.") from exc
    return {
        "success": True,
        "project": {**project, "scene_count": len(scene_rows)},
        "notice": "Project and scene plan saved. Scene generation and final video assembly are separate production steps.",
    }


class VideoProjectSelectionRequest(BaseModel):
    scene_ids: list[UUID] = Field(max_length=20)


@router.get("/projects/list")
async def list_video_projects(
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """List the authenticated user's saved video projects for workspace recovery."""
    db = _db(client)
    found = db.table("video_projects").select(
        "id,title,format,target_duration_seconds,aspect_ratio,visual_style,presenter_mode,status,character_bible,final_video_path,created_at,updated_at,prompt,selected_scene_ids"
    ).eq("owner_id", owner_id).order("created_at", desc=True).limit(50).execute()
    return {"success": True, "projects": _rows(found)}


@router.patch("/projects/{project_id}/selection")
async def update_video_project_selection(
    project_id: UUID,
    body: VideoProjectSelectionRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Persist selected scene IDs without triggering paid generation."""
    db = _db(client)
    project = _rows(db.table("video_projects").select("id").eq(
        "id", str(project_id)
    ).eq("owner_id", owner_id).limit(1).execute())
    if not project:
        raise HTTPException(status_code=404, detail="Video project not found.")
    scene_rows = _rows(db.table("video_project_scenes").select("id").eq(
        "project_id", str(project_id)
    ).eq("owner_id", owner_id).execute())
    allowed = {str(row["id"]) for row in scene_rows}
    requested = [str(scene_id) for scene_id in body.scene_ids]
    if len(set(requested)) != len(requested):
        raise HTTPException(status_code=422, detail="scene_ids must be unique.")
    if any(scene_id not in allowed for scene_id in requested):
        raise HTTPException(status_code=404, detail="One or more selected scenes were not found in this project.")
    updated = _rows(db.table("video_projects").update({
        "selected_scene_ids": requested,
    }).eq("id", str(project_id)).eq("owner_id", owner_id).execute())
    if not updated:
        raise HTTPException(status_code=502, detail="AEA could not confirm the saved scene selection.")
    return {"success": True, "project_id": str(project_id), "selected_scene_ids": requested}


@router.get("/projects/{project_id}")
async def get_video_project(
    project_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    db = _db(client)
    found = db.table("video_projects").select("*").eq("id", str(project_id)).eq("owner_id", owner_id).limit(1).execute()
    projects = _rows(found)
    if not projects:
        raise HTTPException(status_code=404, detail="Video project not found.")
    scenes_response = db.table("video_project_scenes").select(
        "id,scene_number,title,narration,visual_prompt,duration_seconds,status,generation_job_id,video_url,video_storage_path,provider_error,http_status"
    ).eq("project_id", str(project_id)).eq("owner_id", owner_id).order("scene_number").execute()
    scenes = _rows(scenes_response)
    for scene in scenes:
        if scene.get("video_storage_path"):
            scene["video_url"] = _create_video_signed_url(client, scene["video_storage_path"])
    return {"success": True, "project": projects[0], "scenes": scenes}


async def _recover_legacy_scene_asset(
    db: Any,
    client: Any,
    owner_id: str,
    project_id: UUID,
    scene: dict[str, Any],
) -> str:
    """Recover an old completed Runway scene into private storage without new generation."""
    scene_id = str(scene["id"])
    job_id = str(scene.get("generation_job_id") or scene_id)
    job_rows = []
    if scene.get("generation_job_id"):
        job_rows = _rows(db.table("video_generation_jobs").select(
            "id,provider_task_id,status,video_url,video_storage_path,provider_response"
        ).eq("id", str(scene["generation_job_id"])).eq("owner_id", owner_id).limit(1).execute())
    job = job_rows[0] if job_rows else {}
    existing_path = scene.get("video_storage_path") or job.get("video_storage_path")
    if existing_path:
        if not scene.get("video_storage_path"):
            db.table("video_project_scenes").update({
                "video_storage_path": existing_path,
                "video_url": None,
            }).eq("id", scene_id).eq("project_id", str(project_id)).eq("owner_id", owner_id).execute()
        return str(existing_path)

    source_url = scene.get("video_url") or job.get("video_url")
    task: dict[str, Any] | None = None
    if job.get("provider_task_id"):
        headers = _headers()
        try:
            async with httpx.AsyncClient(timeout=30.0) as http:
                response = await http.get(
                    f"{RUNWAY_BASE_URL}/tasks/{job['provider_task_id']}",
                    headers=headers,
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Could not recover the existing Runway video task.") from exc
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail=f"Runway recovery check failed (HTTP {response.status_code}).")
        try:
            task = response.json()
        except ValueError as exc:
            raise HTTPException(status_code=502, detail="Runway returned an invalid recovery response.") from exc
        if str(task.get("status", "")).upper() not in {"SUCCEEDED", "COMPLETED"}:
            raise HTTPException(status_code=409, detail="This Runway task is not completed yet; refresh its generation status.")
        output = task.get("output")
        if isinstance(output, list) and output and isinstance(output[0], str):
            source_url = output[0]

    if not isinstance(source_url, str) or not source_url:
        raise HTTPException(status_code=404, detail="No recoverable video source exists for this scene.")
    try:
        object_path = await _persist_runway_video(
            client,
            owner_id,
            job_id,
            source_url,
            str(project_id),
            scene_id,
        )
        if job:
            db.table("video_generation_jobs").update({
                "status": "completed",
                "video_storage_path": object_path,
                "video_url": None,
                **({"provider_response": task} if task is not None else {}),
            }).eq("id", str(job["id"])).eq("owner_id", owner_id).execute()
        db.table("video_project_scenes").update({
            "status": "completed",
            "video_storage_path": object_path,
            "video_url": None,
        }).eq("id", scene_id).eq("project_id", str(project_id)).eq("owner_id", owner_id).execute()
        return object_path
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(
            status_code=502,
            detail="AEA could not recover this existing clip into permanent storage. No new generation was started.",
        ) from exc


@router.get("/projects/{project_id}/scenes/{scene_id}/video")
async def get_video_project_scene_video_url(
    project_id: UUID,
    scene_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Return a fresh signed URL, migrating recoverable legacy Runway clips on demand."""
    db = _db(client)
    rows = _rows(db.table("video_project_scenes").select(
        "id,project_id,owner_id,status,generation_job_id,video_url,video_storage_path"
    ).eq("id", str(scene_id)).eq("project_id", str(project_id)).eq(
        "owner_id", owner_id
    ).limit(1).execute())
    if not rows:
        raise HTTPException(status_code=404, detail="Video scene not found.")
    scene = rows[0]
    object_path = scene.get("video_storage_path")
    if not object_path:
        object_path = await _recover_legacy_scene_asset(db, client, owner_id, project_id, scene)
    return {
        "success": True,
        "video_url": _create_video_signed_url(client, str(object_path)),
        "expires_in_seconds": 900,
        "storage": "private",
    }


@router.get("/projects/{project_id}/scenes/{scene_id}/video/stream")
async def stream_video_project_scene(
    project_id: UUID,
    scene_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> Response:
    """Serve a private MP4 through the authenticated API, avoiding CDN signed-token playback failures."""
    db = _db(client)
    rows = _rows(db.table("video_project_scenes").select(
        "id,project_id,owner_id,status,generation_job_id,video_url,video_storage_path"
    ).eq("id", str(scene_id)).eq("project_id", str(project_id)).eq(
        "owner_id", owner_id
    ).limit(1).execute())
    if not rows:
        raise HTTPException(status_code=404, detail="Video scene not found.")
    scene = rows[0]
    object_path = scene.get("video_storage_path")
    if not object_path:
        object_path = await _recover_legacy_scene_asset(db, client, owner_id, project_id, scene)
    try:
        video_bytes = client.storage.from_(VIDEO_ASSET_BUCKET).download(str(object_path))
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AEA could not retrieve this private video asset.") from exc
    if not isinstance(video_bytes, (bytes, bytearray)) or not video_bytes:
        raise HTTPException(status_code=502, detail="The stored video asset is empty or unavailable.")
    if len(video_bytes) > MAX_VIDEO_ASSET_BYTES:
        raise HTTPException(status_code=413, detail="The stored video asset exceeds the playback limit.")
    return Response(
        content=bytes(video_bytes),
        media_type="video/mp4",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Disposition": 'inline; filename="aea-scene.mp4"',
            "Accept-Ranges": "none",
        },
    )


@router.post("/projects/{project_id}/scenes/{scene_id}/generate", status_code=status.HTTP_202_ACCEPTED)
async def generate_video_project_scene(
    project_id: UUID,
    scene_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Submit one approved scene prompt to Runway and attach its task to the project."""
    db = _db(client)
    project_response = db.table("video_projects").select(
        "id,owner_id,aspect_ratio,status"
    ).eq("id", str(project_id)).eq("owner_id", owner_id).limit(1).execute()
    projects = _rows(project_response)
    if not projects:
        raise HTTPException(status_code=404, detail="Video project not found.")
    project = projects[0]
    scene_response = db.table("video_project_scenes").select(
        "id,owner_id,visual_prompt,duration_seconds,status,generation_job_id"
    ).eq("id", str(scene_id)).eq("project_id", str(project_id)).eq("owner_id", owner_id).limit(1).execute()
    scenes = _rows(scene_response)
    if not scenes:
        raise HTTPException(status_code=404, detail="Video scene not found.")
    scene = scenes[0]
    if scene.get("generation_job_id") and scene.get("status") != "failed":
        raise HTTPException(status_code=409, detail="This scene already has an active or completed generation job. Only failed scenes can be retried.")
    headers = _headers()
    await _require_runway_credits(headers, "gen4.5", [int(scene["duration_seconds"])])
    payload = {
        "model": "gen4.5",
        "promptText": scene["visual_prompt"],
        "ratio": _ratio(project["aspect_ratio"]),
        "duration": scene["duration_seconds"],
    }
    try:
        async with httpx.AsyncClient(timeout=45.0) as http:
            response = await http.post(f"{RUNWAY_BASE_URL}/text_to_video", headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach the video generation provider.") from exc
    if response.status_code not in (200, 201):
        raise HTTPException(status_code=502, detail=f"Video provider rejected the scene request (HTTP {response.status_code}).")
    try:
        provider_data = response.json()
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="Video provider returned an invalid response.") from exc
    task_id = provider_data.get("id")
    if not isinstance(task_id, str) or not task_id:
        raise HTTPException(status_code=502, detail="Video provider did not return a task ID.")

    job_id = str(uuid4())
    job = {
        "id": job_id,
        "owner_id": owner_id,
        "provider": "runway",
        "provider_task_id": task_id,
        "model": "gen4.5",
        "prompt": scene["visual_prompt"],
        "duration_seconds": scene["duration_seconds"],
        "aspect_ratio": project["aspect_ratio"],
        "status": "pending",
        "provider_response": provider_data,
    }
    try:
        saved_job = db.table("video_generation_jobs").insert(job).execute()
        if not _rows(saved_job):
            raise RuntimeError("Generation job insert returned no row")
        updated_scene = db.table("video_project_scenes").update({
            "generation_job_id": job_id,
            "status": "generating",
        }).eq("id", str(scene_id)).eq("project_id", str(project_id)).eq("owner_id", owner_id).execute()
        if not _rows(updated_scene):
            raise RuntimeError("Scene update returned no row")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Runway task started, but AEA could not attach it to the scene. Contact support for reconciliation.") from exc
    return {
        "success": True,
        "project_id": str(project_id),
        "scene_id": str(scene_id),
        "job": {"id": job_id, "status": "pending", "provider": "runway", "model": "gen4.5"},
        "notice": "Scene generation started. Poll the job endpoint to retrieve its result.",
    }


class VideoProjectPlanRequest(BaseModel):
    prompt: str = Field(min_length=10, max_length=12000)
    title: str = Field(default="Untitled video project", min_length=1, max_length=180)
    format: Literal["short", "long_form", "storytelling", "documentary", "advertisement"] = "storytelling"
    target_duration_seconds: int = Field(default=300, ge=15, le=7200)
    aspect_ratio: Literal["9:16", "16:9"] = "16:9"
    visual_style: str = Field(default="cinematic_realistic", max_length=80)
    presenter_mode: Literal["faceless", "realistic_human", "mixed"] = "faceless"
    language: str = Field(default="Kiswahili", min_length=2, max_length=40)
    audience: str = Field(default="General audience", max_length=500)
    objective: str = Field(default="Storytelling and audience retention", max_length=300)
    model_config = ConfigDict(extra="forbid")


class PlannedVideoScene(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    narration: str = Field(default="", max_length=12000)
    visual_prompt: str = Field(min_length=1, max_length=1000)
    duration_seconds: int = Field(ge=2, le=10)


def _create_project_rows(
    db: Any,
    owner_id: str,
    project: dict[str, Any],
    scenes: list[PlannedVideoScene],
) -> tuple[str, list[dict[str, Any]]]:
    project_id = str(uuid4())
    project_row = {"id": project_id, "owner_id": owner_id, **project}
    scene_rows = [{
        "id": str(uuid4()),
        "project_id": project_id,
        "owner_id": owner_id,
        "scene_number": index,
        "title": scene.title,
        "narration": scene.narration,
        "visual_prompt": scene.visual_prompt,
        "duration_seconds": scene.duration_seconds,
        "status": "pending",
    } for index, scene in enumerate(scenes, start=1)]
    try:
        created = db.table("video_projects").insert(project_row).execute()
        if not _rows(created):
            raise RuntimeError("Project insert returned no row")
        saved_scenes = db.table("video_project_scenes").insert(scene_rows).execute()
        if len(_rows(saved_scenes)) != len(scene_rows):
            raise RuntimeError("Scene insert count mismatch")
    except Exception as exc:
        try:
            db.table("video_projects").delete().eq("id", project_id).eq("owner_id", owner_id).execute()
        except Exception:
            pass
        raise HTTPException(status_code=502, detail="AEA could not persist the complete video project and its scenes.") from exc
    return project_id, scene_rows


@router.post("/projects/plan", status_code=status.HTTP_201_CREATED)
def plan_video_project_from_prompt(
    body: VideoProjectPlanRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Turn one user prompt into an editable, owner-scoped scene-by-scene video plan."""
    db = _db(client)
    provider = get_llm_provider()
    provider_name = str(getattr(provider, "name", "") or provider.__class__.__name__).lower()
    if provider_name in {"mockllmprovider", "mock"}:
        raise HTTPException(
            status_code=503,
            detail="Video story planning needs a live LLM provider. Configure GROQ_API_KEY and LLM_PROVIDER=groq.",
        )
    minimum_scene_count = (body.target_duration_seconds + 9) // 10
    brief = {
        "user_prompt": body.prompt,
        "title": body.title,
        "format": body.format,
        "target_duration_seconds": body.target_duration_seconds,
        "minimum_scene_count": minimum_scene_count,
        "aspect_ratio": body.aspect_ratio,
        "visual_style": body.visual_style,
        "presenter_mode": body.presenter_mode,
        "language": body.language,
        "audience": body.audience,
        "objective": body.objective,
        "production_rules": [
            "Create a complete beginning, middle and ending, not a short teaser.",
            "Return a character_bible object describing each recurring fictional character with stable appearance, age range, clothing, voice style and role; reference these exact descriptors consistently in scene prompts.",
            "Plan for the full target duration, not a teaser or summary. For a 300-second target, create at least 30 scenes.",
            "Break the runtime into ordered scenes, each 2 to 10 seconds because each scene is generated separately.",
            "The sum of all duration_seconds must equal the target_duration_seconds as closely as possible and must never be below 90 percent of it.",
            "Keep recurring characters visually consistent; repeat stable character appearance, clothing and age descriptors in every relevant visual_prompt.",
            "For faceless mode, do not show a speaking presenter or visible identifiable face.",
            "For realistic_human or mixed, describe natural human appearance, expressions, gestures and camera movement.",
            "Write narration in the requested language. Visual prompts should be detailed and cinematic.",
            "Avoid unsupported factual claims and fabricated testimonials. Clearly fictionalize invented story characters.",
            "Return JSON only, with keys title, scenes, creative_notes. scenes is an array of objects with title, narration, visual_prompt, duration_seconds."
        ],
    }
    try:
        generated = provider.generate(
            prompt=json.dumps(brief, ensure_ascii=False),
            system_prompt=(
                "You are AEA's professional long-form video director and storyboard writer. "
                "Create production-ready scene plans for AI video generation. "
                "Respect the requested full duration, audience, language and presenter mode. "
                "Return only valid JSON. Never claim video clips have already been generated."
            ),
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AI story planner failed to generate the video plan.") from exc
    if not isinstance(generated, dict) or generated.get("success") is False:
        # Keep provider diagnostics out of the public response; detailed errors
        # remain available to server-side logs without exposing credentials.
        raise HTTPException(
            status_code=502,
            detail="AI story planner could not obtain a completion from the configured provider. Check Groq model availability and server logs.",
        )
    raw = generated.get("content")
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(
            status_code=502,
            detail="AI provider returned an empty completion. Check the configured Groq model and retry planning.",
        )
    def parse_scenes(content: str) -> tuple[dict[str, Any], list[PlannedVideoScene]]:
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("scenes"), list):
            raise ValueError("Expected a scenes array")
        parsed_scenes = [PlannedVideoScene.model_validate(item) for item in parsed["scenes"]]
        if not parsed_scenes or len(parsed_scenes) > 720:
            raise ValueError("Scene plan must contain between 1 and 720 scenes")
        return parsed, parsed_scenes

    try:
        data, scenes = parse_scenes(raw)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AI returned an invalid scene plan. Please retry with a more specific prompt.") from exc

    planned_seconds = sum(scene.duration_seconds for scene in scenes)
    minimum_seconds = int(body.target_duration_seconds * 0.9)
    if planned_seconds < minimum_seconds:
        # One bounded repair call gives the model the exact deficit and scene-count
        # target rather than asking the user to repeat the same failed request.
        repair_brief = {
            **brief,
            "repair_instruction": (
                f"The previous plan totalled {planned_seconds} seconds, below the required "
                f"{minimum_seconds} seconds. Regenerate the complete storyboard with at least "
                f"{minimum_scene_count} scenes, each 2-10 seconds, targeting exactly "
                f"{body.target_duration_seconds} seconds. Do not summarize or shorten the story. "
                "Return the full JSON object with every scene."
            ),
            "previous_plan": data,
        }
        try:
            repaired = provider.generate(
                prompt=json.dumps(repair_brief, ensure_ascii=False),
                system_prompt=(
                    "You are repairing an incomplete long-form video storyboard. "
                    "Meet the requested runtime and scene count exactly; do not return a summary. "
                    "Return only valid JSON."
                ),
            )
            if not isinstance(repaired, dict) or repaired.get("success") is False:
                raise ValueError("Provider could not repair the short storyboard")
            repaired_content = repaired.get("content")
            if not isinstance(repaired_content, str) or not repaired_content.strip():
                raise ValueError("Provider returned an empty repaired storyboard")
            data, scenes = parse_scenes(repaired_content)
            planned_seconds = sum(scene.duration_seconds for scene in scenes)
        except Exception as exc:
            raise HTTPException(
                status_code=502,
                detail="AI could not repair the storyboard to the requested duration. Retry planning or reduce the target duration.",
            ) from exc

    if planned_seconds < minimum_seconds:
        raise HTTPException(
            status_code=502,
            detail=f"AI plan is still too short ({planned_seconds}s) for the requested {body.target_duration_seconds}s after one repair attempt.",
        )
    project_id, scene_rows = _create_project_rows(
        db,
        owner_id,
        {
            "title": str(data.get("title") or body.title)[:180],
            "prompt": body.prompt,
            "selected_scene_ids": [],
            "character_bible": data.get("character_bible", {}),
            "format": body.format,
            "target_duration_seconds": body.target_duration_seconds,
            "aspect_ratio": body.aspect_ratio,
            "visual_style": body.visual_style,
            "presenter_mode": body.presenter_mode,
            "status": "ready",
        },
        scenes,
    )
    return {
        "success": True,
        "project": {
            "id": project_id,
            "title": str(data.get("title") or body.title)[:180],
            "format": body.format,
            "target_duration_seconds": body.target_duration_seconds,
            "planned_duration_seconds": planned_seconds,
            "scene_count": len(scene_rows),
            "status": "ready",
            "presenter_mode": body.presenter_mode,
            "character_bible": data.get("character_bible", {}),
            "aspect_ratio": body.aspect_ratio,
        },
        "scenes": scene_rows,
        "creative_notes": data.get("creative_notes", []),
        "published": False,
        "notice": "Storyboard saved as an editable draft. Review the scenes before generating paid Runway clips.",
    }


class VideoBatchGenerationRequest(BaseModel):
    scene_ids: list[UUID] = Field(min_length=1, max_length=20)
    model: Literal["gen4.5"] = "gen4.5"


@router.post("/projects/{project_id}/generate-batch", status_code=status.HTTP_202_ACCEPTED)
async def generate_video_project_batch(
    project_id: UUID,
    body: VideoBatchGenerationRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Start a bounded batch of scene tasks; users explicitly opt into paid generation."""
    db = _db(client)
    projects = _rows(db.table("video_projects").select(
        "id,owner_id,aspect_ratio,status"
    ).eq("id", str(project_id)).eq("owner_id", owner_id).limit(1).execute())
    if not projects:
        raise HTTPException(status_code=404, detail="Video project not found.")
    project = projects[0]
    if len(set(body.scene_ids)) != len(body.scene_ids):
        raise HTTPException(status_code=422, detail="scene_ids must be unique.")
    scenes_response = db.table("video_project_scenes").select(
        "id,owner_id,visual_prompt,duration_seconds,status,generation_job_id,scene_number,video_url"
    ).eq("project_id", str(project_id)).eq("owner_id", owner_id).execute()
    by_id = {str(scene["id"]): scene for scene in _rows(scenes_response)}
    missing = [str(scene_id) for scene_id in body.scene_ids if str(scene_id) not in by_id]
    if missing:
        raise HTTPException(status_code=404, detail="One or more scenes were not found in this project.")
    selected = [by_id[str(scene_id)] for scene_id in body.scene_ids]
    # Completed clips are immutable draft assets, not generation queue items.
    # Active jobs are also skipped to avoid duplicate billable provider tasks.
    eligible: list[dict[str, Any]] = []
    results: list[dict[str, Any]] = []
    for scene in selected:
        scene_id = str(scene["id"])
        scene_status = str(scene.get("status") or "pending").lower()
        if scene_status == "completed" or scene.get("video_url"):
            results.append({"scene_id": scene_id, "scene_number": scene.get("scene_number"), "status": "skipped_completed"})
        elif scene.get("generation_job_id") and scene_status != "failed":
            results.append({"scene_id": scene_id, "scene_number": scene.get("scene_number"), "status": "skipped_active"})
        elif scene_status in {"pending", "failed"}:
            eligible.append(scene)
        else:
            results.append({"scene_id": scene_id, "scene_number": scene.get("scene_number"), "status": "skipped_not_retryable"})
    if not eligible:
        return {
            "success": True,
            "requested": len(selected),
            "started": 0,
            "results": results,
            "notice": "No new Runway jobs were started. Completed clips remain in drafts; active or non-retryable scenes were skipped.",
        }
    headers = _headers()
    # Check credits only for eligible scenes and before the first paid request.
    await _require_runway_credits(headers, body.model, [int(scene["duration_seconds"]) for scene in eligible])
    async with httpx.AsyncClient(timeout=45.0) as http:
        for scene in eligible:
            payload = {
                "model": body.model,
                "promptText": scene["visual_prompt"],
                "ratio": _ratio(project["aspect_ratio"]),
                "duration": scene["duration_seconds"],
            }
            try:
                response = await http.post(f"{RUNWAY_BASE_URL}/text_to_video", headers=headers, json=payload)
            except httpx.HTTPError as exc:
                diagnostic = {"error": "provider_transport_error", "message": str(exc)[:500] or "Could not reach Runway."}
                try:
                    _persist_scene_failure(db, owner_id, project_id, str(scene["id"]), diagnostic)
                except Exception:
                    results.append({"scene_id": scene["id"], "status": "diagnostic_persistence_error"})
                    continue
                results.append({"scene_id": scene["id"], "status": "provider_error", "provider_error": diagnostic})
                continue
            if response.status_code not in (200, 201):
                # Preserve a bounded, useful provider diagnostic without exposing
                # request headers, API credentials, or an unbounded response body.
                try:
                    provider_error = response.json()
                except ValueError:
                    provider_error = {"message": response.text[:1200]}
                if isinstance(provider_error, dict):
                    allowed_keys = ("error", "message", "detail", "issues", "code", "type", "title")
                    diagnostic = {
                        key: provider_error[key]
                        for key in allowed_keys
                        if key in provider_error
                    }
                    if not diagnostic:
                        diagnostic = {"message": json.dumps(provider_error, ensure_ascii=False)[:1200]}
                else:
                    diagnostic = {"message": str(provider_error)[:1200]}
                # Persist the rejection so refresh/recovery does not erase the
                # actionable provider diagnostic from the project scene.
                try:
                    _persist_scene_failure(db, owner_id, project_id, str(scene["id"]), diagnostic, response.status_code)
                except Exception:
                    results.append({"scene_id": scene["id"], "status": "diagnostic_persistence_error", "http_status": response.status_code})
                    continue
                results.append({
                    "scene_id": scene["id"],
                    "scene_number": scene.get("scene_number"),
                    "status": "provider_rejected",
                    "http_status": response.status_code,
                    "provider_error": diagnostic,
                })
                continue
            try:
                provider_data = response.json()
            except ValueError:
                diagnostic = {"error": "invalid_provider_response", "message": "Runway returned a non-JSON response."}
                try:
                    _persist_scene_failure(db, owner_id, project_id, str(scene["id"]), diagnostic, response.status_code)
                except Exception:
                    results.append({"scene_id": scene["id"], "status": "diagnostic_persistence_error", "http_status": response.status_code})
                    continue
                results.append({"scene_id": scene["id"], "status": "invalid_provider_response", "provider_error": diagnostic, "http_status": response.status_code})
                continue
            task_id = provider_data.get("id")
            if not isinstance(task_id, str) or not task_id:
                diagnostic = {"error": "missing_task_id", "message": "Runway response did not contain a task ID."}
                try:
                    _persist_scene_failure(db, owner_id, project_id, str(scene["id"]), diagnostic, response.status_code)
                except Exception:
                    results.append({"scene_id": scene["id"], "status": "diagnostic_persistence_error", "http_status": response.status_code})
                    continue
                results.append({"scene_id": scene["id"], "status": "missing_task_id", "provider_error": diagnostic, "http_status": response.status_code})
                continue
            job_id = str(uuid4())
            job = {
                "id": job_id,
                "owner_id": owner_id,
                "provider": "runway",
                "provider_task_id": task_id,
                "model": body.model,
                "prompt": scene["visual_prompt"],
                "duration_seconds": scene["duration_seconds"],
                "aspect_ratio": project["aspect_ratio"],
                "status": "pending",
                "provider_response": provider_data,
            }
            try:
                saved = db.table("video_generation_jobs").insert(job).execute()
                if not _rows(saved):
                    raise RuntimeError("Job not persisted")
                updated = db.table("video_project_scenes").update({
                    "generation_job_id": job_id,
                    "status": "generating",
                    "provider_error": None,
                    "http_status": None,
                }).eq("id", str(scene["id"])).eq("project_id", str(project_id)).eq("owner_id", owner_id).execute()
                if not _rows(updated):
                    raise RuntimeError("Scene update not persisted")
            except Exception:
                results.append({"scene_id": scene["id"], "status": "persistence_error", "provider_task_id": task_id})
                continue
            results.append({"scene_id": scene["id"], "job_id": job_id, "status": "pending"})
    started = sum(1 for result in results if result["status"] == "pending")
    return {
        "success": started > 0,
        "project_id": str(project_id),
        "requested": len(selected),
        "started": started,
        "results": results,
        "notice": "Only the selected scenes were submitted. Check provider usage and costs; this endpoint does not assemble a final MP4.",
    }


def _srt_timestamp(milliseconds: int) -> str:
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, millis = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"


@router.get("/projects/{project_id}/subtitles.srt")
def export_video_project_subtitles(
    project_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> Response:
    """Export scene narration as a timed SRT subtitle track for a video editor."""
    db = _db(client)
    projects = _rows(db.table("video_projects").select(
        "id,target_duration_seconds"
    ).eq("id", str(project_id)).eq("owner_id", owner_id).limit(1).execute())
    if not projects:
        raise HTTPException(status_code=404, detail="Video project not found.")
    scenes = _rows(db.table("video_project_scenes").select(
        "scene_number,narration,duration_seconds"
    ).eq("project_id", str(project_id)).eq("owner_id", owner_id).order("scene_number").execute())
    if not scenes:
        raise HTTPException(status_code=409, detail="This video project has no scenes to subtitle.")
    cursor_ms = 0
    cues: list[str] = []
    for scene in scenes:
        narration = str(scene.get("narration") or "").strip()
        duration_ms = int(scene.get("duration_seconds") or 0) * 1000
        if narration and duration_ms > 0:
            # A cue per scene gives editors a reliable first-pass timed subtitle track.
            start = cursor_ms
            end = cursor_ms + duration_ms
            cues.append(
                f"{len(cues) + 1}\n{_srt_timestamp(start)} --> {_srt_timestamp(end)}\n{narration}"
            )
        cursor_ms += duration_ms
    if not cues:
        raise HTTPException(status_code=409, detail="No scene narration is available to export as subtitles.")
    content = "\n\n".join(cues) + "\n"
    return Response(
        content=content,
        media_type="application/x-subrip; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="aea-video-{project_id}.srt"'},
    )


@router.get("/narration/config")
def get_narration_config(
    owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Expose only non-secret ElevenLabs readiness and the configured voice identifier."""
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    voice_id = (
        os.getenv("ELEVENLABS_DEFAULT_VOICE_ID", "").strip()
        or os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    )
    return {
        "success": True,
        "configured": bool(api_key and voice_id),
        "api_key_configured": bool(api_key),
        "voice_id_configured": bool(voice_id),
        "model_id": "eleven_v3",
        "language_code": "sw",
    }


class SceneNarrationRequest(BaseModel):
    voice_id: str = Field(default="", max_length=100)
    model_id: Literal["eleven_v3"] = "eleven_v3"
    language_code: Literal["sw", "en"] = "sw"
    delivery: Literal["natural", "warm", "dramatic", "documentary"] = "natural"
    model_config = ConfigDict(extra="forbid")


@router.post("/projects/{project_id}/scenes/{scene_id}/narration", status_code=status.HTTP_201_CREATED)
async def generate_scene_narration_audio(
    project_id: UUID,
    scene_id: UUID,
    body: SceneNarrationRequest,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Generate one explicitly requested ElevenLabs narration clip and store it privately."""
    db = _db(client)
    api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        raise HTTPException(status_code=503, detail="Narration audio is not configured. Add ELEVENLABS_API_KEY to AEA Core.")
    voice_id = (
        os.getenv("ELEVENLABS_DEFAULT_VOICE_ID", "").strip()
        or os.getenv("ELEVENLABS_VOICE_ID", "").strip()
    )
    project_rows = _rows(db.table("video_projects").select("id").eq(
        "id", str(project_id)
    ).eq("owner_id", owner_id).limit(1).execute())
    if not project_rows:
        raise HTTPException(status_code=404, detail="Video project not found.")
    scene_rows = _rows(db.table("video_project_scenes").select(
        "id,narration,narration_audio_path"
    ).eq("id", str(scene_id)).eq("project_id", str(project_id)).eq(
        "owner_id", owner_id
    ).limit(1).execute())
    if not scene_rows:
        raise HTTPException(status_code=404, detail="Video scene not found.")
    scene = scene_rows[0]
    narration = str(scene.get("narration") or "").strip()
    if not narration:
        raise HTTPException(status_code=409, detail="This scene has no narration text.")
    if len(narration) > 5000:
        raise HTTPException(status_code=422, detail="Eleven v3 supports up to 5,000 characters per request. Split this scene narration before generating audio.")
    if not voice_id:
        raise HTTPException(status_code=503, detail="ElevenLabs Voice ID is not configured. Add ELEVENLABS_DEFAULT_VOICE_ID or ELEVENLABS_VOICE_ID to AEA Core.")
    delivery_style = {
        "natural": 0.25,
        "warm": 0.4,
        "dramatic": 0.65,
        "documentary": 0.2,
    }[body.delivery]
    payload = {
        "text": narration,
        "model_id": body.model_id,
        "language_code": body.language_code,
        "voice_settings": {"stability": 0.48, "similarity_boost": 0.78, "style": delivery_style, "use_speaker_boost": True},
    }
    try:
        async with httpx.AsyncClient(timeout=90.0) as http:
            response = await http.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
                headers={"xi-api-key": api_key, "Content-Type": "application/json"},
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach the narration provider.") from exc
    if response.status_code != 200 or not response.content:
        raise HTTPException(status_code=502, detail=f"Narration provider rejected the request (HTTP {response.status_code}). Check voice access, model availability and account billing.")
    storage_path = f"{owner_id}/{project_id}/{scene_id}.mp3"
    try:
        client.storage.from_("aea-video-assets").upload(
            storage_path,
            response.content,
            file_options={"content-type": "audio/mpeg", "upsert": "true"},
        )
        saved = db.table("video_project_scenes").update({
            "narration_audio_path": storage_path,
        }).eq("id", str(scene_id)).eq("project_id", str(project_id)).eq(
            "owner_id", owner_id
        ).select("id").execute()
        if not _rows(saved):
            raise RuntimeError("Audio path was not saved on the scene.")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Narration was generated, but AEA could not securely store or attach the audio file.") from exc
    return {
        "success": True,
        "project_id": str(project_id),
        "scene_id": str(scene_id),
        "audio": {"storage_path": storage_path, "format": "mp3", "model": body.model_id, "language_code": body.language_code},
        "notice": "AI-generated narration audio saved privately. It has not yet been mixed into the final video.",
    }


@router.get("/projects/{project_id}/scenes/{scene_id}/narration")
def get_scene_narration_audio_url(
    project_id: UUID,
    scene_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Return a short-lived signed URL for the current user's private narration clip."""
    db = _db(client)
    rows = _rows(db.table("video_project_scenes").select(
        "id,narration_audio_path"
    ).eq("id", str(scene_id)).eq("project_id", str(project_id)).eq(
        "owner_id", owner_id
    ).limit(1).execute())
    if not rows:
        raise HTTPException(status_code=404, detail="Video scene not found.")
    path = rows[0].get("narration_audio_path")
    if not path:
        raise HTTPException(status_code=404, detail="Narration audio has not been generated for this scene.")
    try:
        signed = client.storage.from_("aea-video-assets").create_signed_url(path, 900)
        url = signed.get("signedURL") or signed.get("signedUrl")
        if not url:
            raise RuntimeError("Storage did not return a signed URL.")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not create a private narration download link.") from exc
    return {"success": True, "audio_url": url, "expires_in_seconds": 900}


def _assemble_project_video(project_id: str, owner_id: str, client: Any) -> bool:
    """Render completed scene clips and optional narration into one private MP4."""
    db = _db(client)
    temp_root: Path | None = None
    try:
        project_rows = _rows(db.table("video_projects").select(
            "id,aspect_ratio"
        ).eq("id", project_id).eq("owner_id", owner_id).limit(1).execute())
        if not project_rows:
            raise RuntimeError("Video project not found for the queued owner.")
        project = project_rows[0]
        scenes = _rows(db.table("video_project_scenes").select(
            "id,scene_number,duration_seconds,status,video_url,video_storage_path,narration_audio_path"
        ).eq("project_id", project_id).eq("owner_id", owner_id).order("scene_number").execute())
        if not scenes or any(s.get("status") != "completed" or not (s.get("video_storage_path") or s.get("video_url")) for s in scenes):
            raise RuntimeError("Every scene must have a completed video before assembly.")
        temp_root = Path(tempfile.mkdtemp(prefix="aea-video-"))
        ffmpeg = __import__("imageio_ffmpeg").get_ffmpeg_exe()
        segment_paths: list[Path] = []
        width, height = (720, 1280) if project.get("aspect_ratio") == "9:16" else (1280, 720)

        with httpx.Client(timeout=httpx.Timeout(120.0, connect=20.0), follow_redirects=True) as http:
            for index, scene in enumerate(scenes, start=1):
                video_path = temp_root / f"scene-{index:04d}-source.mp4"
                if scene.get("video_storage_path"):
                    video_bytes = client.storage.from_(VIDEO_ASSET_BUCKET).download(scene["video_storage_path"])
                    video_path.write_bytes(video_bytes)
                else:
                    # Backward compatibility for legacy projects created before private asset persistence.
                    video_response = http.get(scene["video_url"])
                    video_response.raise_for_status()
                    video_path.write_bytes(video_response.content)
                segment_path = temp_root / f"scene-{index:04d}.mp4"
                command = [ffmpeg, "-y", "-i", str(video_path)]
                narration_path = None
                if scene.get("narration_audio_path"):
                    audio_bytes = client.storage.from_("aea-video-assets").download(scene["narration_audio_path"])
                    narration_path = temp_root / f"scene-{index:04d}.mp3"
                    narration_path.write_bytes(audio_bytes)
                    command += ["-i", str(narration_path)]
                    audio_map = ["-map", "1:a:0"]
                else:
                    command += ["-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"]
                    audio_map = ["-map", "1:a:0"]
                duration = max(2, min(10, int(scene["duration_seconds"])))
                command += [
                    "-map", "0:v:0", *audio_map,
                    "-vf", f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=24",
                    "-t", str(duration), "-c:v", "libx264", "-preset", "veryfast",
                    "-crf", "23", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    "-b:a", "128k", "-ar", "44100", "-ac", "2",
                    "-movflags", "+faststart", str(segment_path),
                ]
                rendered = subprocess.run(command, capture_output=True, text=True, timeout=240)
                if rendered.returncode != 0 or not segment_path.exists():
                    raise RuntimeError(f"FFmpeg failed while rendering scene {index}.")
                segment_paths.append(segment_path)

        concat_file = temp_root / "concat.txt"
        concat_file.write_text("".join(f"file '{p.as_posix()}'\n" for p in segment_paths), encoding="utf-8")
        output_path = temp_root / "final.mp4"
        rendered = subprocess.run(
            [ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat_file),
             "-c", "copy", "-movflags", "+faststart", str(output_path)],
            capture_output=True, text=True, timeout=600,
        )
        if rendered.returncode != 0 or not output_path.exists():
            raise RuntimeError("FFmpeg could not join the rendered scene files.")
        storage_path = f"{owner_id}/{project_id}/final.mp4"
        client.storage.from_("aea-video-assets").upload(
            storage_path, output_path.read_bytes(),
            file_options={"content-type": "video/mp4", "upsert": "true"},
        )
        saved = db.table("video_projects").update({
            "status": "completed",
            "final_video_path": storage_path,
            "assembly_error": None,
        }).eq("id", project_id).eq("owner_id", owner_id).select("id").execute()
        if not _rows(saved):
            raise RuntimeError("Final video was uploaded but project status could not be saved.")
        return True
    except Exception as exc:
        try:
            db.table("video_projects").update({
                "status": "failed",
                "assembly_error": str(exc)[:1000],
            }).eq("id", project_id).eq("owner_id", owner_id).execute()
        except Exception:
            pass
        return False
    finally:
        if temp_root is not None:
            import shutil
            shutil.rmtree(temp_root, ignore_errors=True)


@router.post("/projects/{project_id}/assemble", status_code=status.HTTP_202_ACCEPTED)
def assemble_video_project(
    project_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Queue final MP4 assembly after every scene has completed."""
    db = _db(client)
    projects = _rows(db.table("video_projects").select(
        "id,status,final_video_path"
    ).eq("id", str(project_id)).eq("owner_id", owner_id).limit(1).execute())
    if not projects:
        raise HTTPException(status_code=404, detail="Video project not found.")
    project = projects[0]
    if project.get("status") == "assembling":
        raise HTTPException(status_code=409, detail="This project is already being assembled.")
    if project.get("final_video_path"):
        raise HTTPException(status_code=409, detail="This project already has a final MP4. Create a new project to render another version.")
    scenes = _rows(db.table("video_project_scenes").select(
        "id,status,video_url"
    ).eq("project_id", str(project_id)).eq("owner_id", owner_id).execute())
    if not scenes:
        raise HTTPException(status_code=409, detail="This project has no scenes.")
    if any(s.get("status") != "completed" or not s.get("video_url") for s in scenes):
        raise HTTPException(status_code=409, detail="All scenes must finish generating before final assembly.")
    job_id = str(uuid4())
    try:
        queued = db.table("video_assembly_jobs").insert({
            "id": job_id,
            "project_id": str(project_id),
            "owner_id": owner_id,
            "status": "queued",
            "attempts": 0,
            "max_attempts": 3,
        }).execute()
        if not _rows(queued):
            raise RuntimeError("Queue insert returned no row.")
        updated = db.table("video_projects").update({
            "status": "assembling", "assembly_error": None,
        }).eq("id", str(project_id)).eq("owner_id", owner_id).select("id").execute()
        if not _rows(updated):
            raise RuntimeError("Project status update returned no row.")
    except Exception as exc:
        raise HTTPException(status_code=503, detail="AEA could not durably queue final video assembly. Apply the durable video queue migration and retry.") from exc
    return {
        "success": True,
        "project_id": str(project_id),
        "job_id": job_id,
        "status": "queued",
        "notice": "Final MP4 assembly is durably queued. The AEA video worker will process it.",
    }


@router.get("/projects/{project_id}/final-video")
def get_video_project_final_url(
    project_id: UUID,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    """Return a short-lived signed URL for the owner's completed final MP4."""
    db = _db(client)
    projects = _rows(db.table("video_projects").select(
        "id,status,final_video_path,assembly_error"
    ).eq("id", str(project_id)).eq("owner_id", owner_id).limit(1).execute())
    if not projects:
        raise HTTPException(status_code=404, detail="Video project not found.")
    project = projects[0]
    path = project.get("final_video_path")
    if not path:
        return {
            "success": True,
            "status": project.get("status"),
            "error": project.get("assembly_error"),
            "video_url": None,
        }
    try:
        signed = client.storage.from_("aea-video-assets").create_signed_url(path, 900)
        url = signed.get("signedURL") or signed.get("signedUrl")
        if not url:
            raise RuntimeError("Storage did not return a signed URL.")
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Could not create a private final-video link.") from exc
    return {
        "success": True,
        "status": "completed",
        "video_url": url,
        "expires_in_seconds": 900,
    }


__all__ = ["router"]
