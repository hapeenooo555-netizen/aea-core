"""Owner-scoped AI model registry and capability routing."""
from __future__ import annotations
from typing import Any
from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from app.dependencies import get_current_user_id, get_user_scoped_client
router = APIRouter(prefix="/ai/models", tags=["ai-models"])
ALLOWED_CAPABILITIES = {"text","image","video","speech","transcription","subtitles","embeddings"}
class ModelCreate(BaseModel):
    provider: str = Field(min_length=1,max_length=80)
    model_key: str = Field(min_length=1,max_length=160)
    display_name: str = Field(min_length=1,max_length=160)
    capabilities: list[str] = Field(default_factory=list,max_length=12)
    enabled: bool = True
    priority: int = Field(default=100,ge=0,le=10000)
    cost_per_unit: float | None = Field(default=None,ge=0)
    config: dict[str,Any] = Field(default_factory=dict)
    model_config = ConfigDict(extra="forbid")
class ModelPatch(BaseModel):
    display_name: str | None = Field(default=None,min_length=1,max_length=160)
    capabilities: list[str] | None = Field(default=None,max_length=12)
    enabled: bool | None = None
    priority: int | None = Field(default=None,ge=0,le=10000)
    cost_per_unit: float | None = Field(default=None,ge=0)
    config: dict[str,Any] | None = None
    model_config = ConfigDict(extra="forbid")
def _caps(values: list[str]) -> None:
    invalid=sorted(set(values)-ALLOWED_CAPABILITIES)
    if invalid: raise HTTPException(422,detail={"invalid_capabilities":invalid,"allowed":sorted(ALLOWED_CAPABILITIES)})
def _config(values: dict[str,Any]) -> None:
    if {"api_key","secret","token","authorization","password"}.intersection(k.lower() for k in values):
        raise HTTPException(422,detail="Store provider credentials in deployment secrets, not model config")
@router.get("")
def list_models(capability: str|None=Query(default=None),owner_id: str=Depends(get_current_user_id),client: Any=Depends(get_user_scoped_client)):
    if capability and capability not in ALLOWED_CAPABILITIES: raise HTTPException(422,detail="Unsupported capability")
    try: rows=client.table("aea_ai_models").select("*").eq("owner_id",owner_id).execute().data or []
    except Exception as exc: raise HTTPException(503,detail="AI model registry unavailable; apply the registry migration") from exc
    if capability: rows=[r for r in rows if capability in (r.get("capabilities") or [])]
    rows.sort(key=lambda r:(not r.get("enabled",True),r.get("priority",100)))
    return {"success":True,"models":rows}
@router.post("",status_code=status.HTTP_201_CREATED)
def register_model(body: ModelCreate,owner_id: str=Depends(get_current_user_id),client: Any=Depends(get_user_scoped_client)):
    _caps(body.capabilities); _config(body.config)
    payload=body.model_dump(); payload["owner_id"]=owner_id
    try: rows=client.table("aea_ai_models").insert(payload).execute().data or []
    except Exception as exc: raise HTTPException(400,detail="Could not register model; check uniqueness and migration") from exc
    if not rows: raise HTTPException(503,detail="Database did not confirm model creation")
    return {"success":True,"model":rows[0]}
@router.patch("/{model_id}")
def update_model(model_id: str,body: ModelPatch,owner_id: str=Depends(get_current_user_id),client: Any=Depends(get_user_scoped_client)):
    changes=body.model_dump(exclude_unset=True)
    if not changes: raise HTTPException(400,detail="At least one field is required")
    if changes.get("capabilities") is not None: _caps(changes["capabilities"])
    if changes.get("config") is not None: _config(changes["config"])
    try: rows=client.table("aea_ai_models").update(changes).eq("id",model_id).eq("owner_id",owner_id).execute().data or []
    except Exception as exc: raise HTTPException(400,detail="Could not update model") from exc
    if not rows: raise HTTPException(404,detail="Model not found")
    return {"success":True,"model":rows[0]}
@router.get("/route")
def route_model(capability: str=Query(...),owner_id: str=Depends(get_current_user_id),client: Any=Depends(get_user_scoped_client)):
    if capability not in ALLOWED_CAPABILITIES: raise HTTPException(422,detail="Unsupported capability")
    try: rows=client.table("aea_ai_models").select("*").eq("owner_id",owner_id).eq("enabled",True).execute().data or []
    except Exception as exc: raise HTTPException(503,detail="AI model registry unavailable") from exc
    candidates=[r for r in rows if capability in (r.get("capabilities") or [])]
    candidates.sort(key=lambda r:(r.get("priority",100),r.get("cost_per_unit") if r.get("cost_per_unit") is not None else float("inf")))
    if not candidates: raise HTTPException(404,detail=f"No enabled model supports {capability}")
    return {"success":True,"capability":capability,"selected_model":candidates[0],"fallback_models":candidates[1:]}
