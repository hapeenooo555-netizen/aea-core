"""Owner-scoped model registry and capability-based model routing."""
from __future__ import annotations

from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client

router = APIRouter(prefix="/ai/models", tags=["ai-models"])
ALLOWED_CAPABILITIES = {"text", "image", "video", "speech", "transcription", "subtitles", "embeddings"}

class ModelCreate(BaseModel):
    provider: str = Field(min_length=1, max_length=80)
    model_key: str = Field(min_length=1, max_length=160)
    display_name: str = Field(min_length=1, max_length=160)
    capabilities: list[str] = Field(default_factory=list, max_length=12)
    enabled: bool = True
    priority: int = Field(default=100, ge=0, le=10000)
    cost_per_unit: float | None = Field(default=None, ge=0)
    config: dict[str, Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="forbid")

class ModelPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=160)
    capabilities: list[str] | None = Field(default=None, max_length=12)
    enabled: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=10000)
    cost_per_unit: float | None = Field(default=None, ge=0)
    config: dict[str, Any] | None = None
    model_config = ConfigDict(extra="forbid")

def _validate_caps(caps: list[str]) -> None:
    invalid = sorted(set(caps) - ALLOWED_CAPABILITIES)
    if invalid:
        raise HTTPException(status_code=422, detail={"invalid_capabilities": invalid, "allowed": sorted(ALLOWED_CAPABILITIES)})

@router.get("")
def list_models(
    capability: str | None = Query(default=None),
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    if capability and capability not in ALLOWED_CAPABILITIES:
        raise HTTPException(status_code=422, detail="Unsupported capability")
    try:
        query = client.table("aea_ai_models").select("*").eq("owner_id", owner_id)
        rows = query.execute().data or []
    except Exception as exc:
        raise HTTPException(status_code=503, detail="AI model registry unavailable; apply the registry migration") from exc
    if capability:
        rows = [row for row in rows if capability in (row.get("capabilities") or [])]
    return {"success": True, "models": sorted(rows, key=lambda row: (not row.get("enabled", True), row.get("priority", 100)))}

@router.post("", status_code=status.HTTP_201_CREATED)
def register_model(
    body: ModelCreate,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    _validate_caps(body.capabilities)
    # Never accept credentials/API keys in model config.
    forbidden = {"api_key", "secret", "token", "authorization", "password"}
    if forbidden.intersection(k.lower() for k in body.config):
        raise HTTPException(status_code=422, detail="Store provider credentials in deployment secrets, not model config")
    payload = body.model_dump()
    payload["owner_id"] = owner_id
    try:
        result = client.table("aea_ai_models").insert(payload).execute()
        return {"success": True, "model": (result.data or [None])[0]}
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not register model; check uniqueness and registry migration") from exc

@router.patch("/{model_id}")
def update_model(
    model_id: str,
    body: ModelPatch,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="At least one field is required")
    if changes.get("capabilities") is not None:
        _validate_caps(changes["capabilities"])
    if changes.get("config") is not None and {"api_key", "secret", "token", "authorization", "password"}.intersection(k.lower() for k in changes["config"]):
        raise HTTPException(status_code=422, detail="Store credentials in deployment secrets")
    try:
        result = client.table("aea_ai_models").update(changes).eq("id", model_id).eq("owner_id", owner_id).execute()
        rows = result.data or []
        if not rows:
            raise HTTPException(status_code=404, detail="Model not found")
        return {"success": True, "model": rows[0]}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not update model") from exc

@router.get("/route")
def route_model(
    capability: str = Query(...),
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    if capability not in ALLOWED_CAPABILITIES:
        raise HTTPException(status_code=422, detail="Unsupported capability")
    try:
        rows = client.table("aea_ai_models").select("*").eq("owner_id", owner_id).eq("enabled", True).execute().data or []
    except Exception as exc:
        raise HTTPException(status_code=503, detail="AI model registry unavailable") from exc
    candidates = [row for row in rows if capability in (row.get("capabilities") or [])]
    candidates.sort(key=lambda row: (row.get("priority", 100), row.get("cost_per_unit") if row.get("cost_per_unit") is not None else float("inf")))
    if not candidates:
        raise HTTPException(status_code=404, detail=f"No enabled model supports {capability}")
    return {"success": True, "capability": capability, "selected_model": candidates[0], "fallback_models": candidates[1:]}
