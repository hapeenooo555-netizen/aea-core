"""Authenticated, owner-scoped API for Freelancer Employee projects."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id, get_user_scoped_client
from app.services.freelancer_projects import (
    PROJECT_STATUSES, FreelancerProjectError, create_project, get_project,
    list_projects, update_project,
)

router = APIRouter(prefix="/freelancer/projects", tags=["freelancer-projects"])


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    job_description: str = Field(min_length=1, max_length=30000)
    source_url: str | None = Field(default=None, max_length=2048)
    status: str = "lead"
    proposal_text: str | None = Field(default=None, max_length=30000)
    agreed_amount: float | None = Field(default=None, ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    amount_received: float = Field(default=0, ge=0)
    notes: str | None = Field(default=None, max_length=10000)
    model_config = ConfigDict(extra="forbid")


class ProjectUpdate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    job_description: str | None = Field(default=None, min_length=1, max_length=30000)
    source_url: str | None = Field(default=None, max_length=2048)
    status: str | None = None
    proposal_text: str | None = Field(default=None, max_length=30000)
    agreed_amount: float | None = Field(default=None, ge=0)
    currency: str | None = Field(default=None, min_length=3, max_length=3)
    amount_received: float | None = Field(default=None, ge=0)
    notes: str | None = Field(default=None, max_length=10000)
    model_config = ConfigDict(extra="forbid")


def _client_error(exc: FreelancerProjectError) -> HTTPException:
    message = str(exc)
    code = status.HTTP_503_SERVICE_UNAVAILABLE if "database" in message.lower() or "supabase migration" in message.lower() else status.HTTP_400_BAD_REQUEST
    return HTTPException(status_code=code, detail=message)


@router.post("", status_code=status.HTTP_201_CREATED)
def create_freelancer_project(
    body: ProjectCreate,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    try:
        return {"success": True, "project": create_project(client, owner_id, body.model_dump())}
    except FreelancerProjectError as exc:
        raise _client_error(exc) from exc


@router.get("")
def list_freelancer_projects(
    status_filter: str | None = Query(default=None, alias="status"),
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    try:
        if status_filter and status_filter not in PROJECT_STATUSES:
            raise FreelancerProjectError("Invalid project status")
        return {"success": True, "projects": list_projects(client, status=status_filter)}
    except FreelancerProjectError as exc:
        raise _client_error(exc) from exc


@router.get("/{project_id}")
def read_freelancer_project(
    project_id: str,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    try:
        project = get_project(client, project_id)
    except FreelancerProjectError as exc:
        raise _client_error(exc) from exc
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True, "project": project}


@router.patch("/{project_id}")
def patch_freelancer_project(
    project_id: str,
    body: ProjectUpdate,
    owner_id: str = Depends(get_current_user_id),
    client: Any = Depends(get_user_scoped_client),
) -> dict[str, Any]:
    changes = body.model_dump(exclude_unset=True)
    if not changes:
        raise HTTPException(status_code=400, detail="At least one field must be supplied")
    try:
        project = update_project(client, project_id, changes)
    except FreelancerProjectError as exc:
        raise _client_error(exc) from exc
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    return {"success": True, "project": project}
