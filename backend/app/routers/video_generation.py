"""Runway-backed video generation jobs for authenticated AEA users.

The API key remains server-side. Generation is asynchronous: clients create a job,
then poll its status. Publishing is deliberately out of scope.
"""
from __future__ import annotations

import json
import os
from typing import Any, Literal
from uuid import UUID, uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.llm_provider import get_llm_provider

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
    # Propagate each scene task's terminal/progress state into its owning project scene.
    try:
        db.table("video_project_scenes").update({
            "status": "completed" if normalized == "completed" else ("failed" if normalized == "failed" else "generating"),
            **({"video_url": video_url} if video_url else {}),
        }).eq("generation_job_id", str(job_id)).eq("owner_id", owner_id).execute()
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AEA could not synchronize the scene status.") from exc
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
    scenes: list[VideoSceneInput] = Field(min_length=1, max_length=500)
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
        "id,scene_number,title,narration,visual_prompt,duration_seconds,status,generation_job_id,video_url"
    ).eq("project_id", str(project_id)).eq("owner_id", owner_id).order("scene_number").execute()
    return {"success": True, "project": projects[0], "scenes": _rows(scenes_response)}


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
    if scene.get("generation_job_id"):
        raise HTTPException(status_code=409, detail="This scene already has a generation job.")
    headers = _headers()
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
    brief = {
        "user_prompt": body.prompt,
        "title": body.title,
        "format": body.format,
        "target_duration_seconds": body.target_duration_seconds,
        "aspect_ratio": body.aspect_ratio,
        "visual_style": body.visual_style,
        "presenter_mode": body.presenter_mode,
        "language": body.language,
        "audience": body.audience,
        "objective": body.objective,
        "production_rules": [
            "Create a complete beginning, middle and ending, not a short teaser.",
            "Break the full requested runtime into ordered scenes, each 2 to 10 seconds because each scene is generated separately.",
            "Use enough scenes for the total scene durations to reach at least 90 percent of the requested runtime.",
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
    raw = generated.get("content") if isinstance(generated, dict) else None
    if not isinstance(raw, str) or not raw.strip():
        raise HTTPException(status_code=502, detail="AI story planner returned an empty plan.")
    try:
        data = json.loads(raw)
        if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
            raise ValueError("Expected a scenes array")
        scenes = [PlannedVideoScene.model_validate(item) for item in data["scenes"]]
    except Exception as exc:
        raise HTTPException(status_code=502, detail="AI returned an invalid scene plan. Please retry with a more specific prompt.") from exc
    if not scenes or len(scenes) > 500:
        raise HTTPException(status_code=502, detail="AI scene plan must contain between 1 and 500 scenes.")
    planned_seconds = sum(scene.duration_seconds for scene in scenes)
    if planned_seconds < int(body.target_duration_seconds * 0.9):
        raise HTTPException(
            status_code=502,
            detail=f"AI plan is too short ({planned_seconds}s) for the requested {body.target_duration_seconds}s. Retry planning.",
        )
    project_id, scene_rows = _create_project_rows(
        db,
        owner_id,
        {
            "title": str(data.get("title") or body.title)[:180],
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
            "aspect_ratio": body.aspect_ratio,
        },
        "scenes": scene_rows,
        "creative_notes": data.get("creative_notes", []),
        "published": False,
        "notice": "Storyboard saved as an editable draft. Review the scenes before generating paid Runway clips.",
    }


__all__ = ["router"]
