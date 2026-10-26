"""Authenticated API for the first AEA Freelancer Employee slice.

Endpoints only analyze user-provided text and create drafts. They do not
connect to or automate any external freelancing marketplace.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.dependencies import get_current_user_id
from app.services.freelancer_employee import analyze_job, draft_proposal

router = APIRouter(prefix="/freelancer", tags=["freelancer-employee"])


class JobAnalysisRequest(BaseModel):
    description: str = Field(min_length=1, max_length=30000)
    source_url: str | None = Field(default=None, max_length=2048)
    model_config = ConfigDict(extra="forbid")


class ProposalDraftRequest(BaseModel):
    description: str = Field(min_length=1, max_length=30000)
    freelancer_name: str | None = Field(default=None, max_length=200)
    relevant_experience: str | None = Field(default=None, max_length=5000)
    approach: str | None = Field(default=None, max_length=5000)
    price_quote: str | None = Field(default=None, max_length=200)
    timeline: str | None = Field(default=None, max_length=200)
    model_config = ConfigDict(extra="forbid")


@router.post("/analyze")
def analyze_freelance_job(
    body: JobAnalysisRequest,
    owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Analyze a job description supplied by the authenticated user."""
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return analyze_job(body.description, source_url=body.source_url)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/proposal-draft")
def create_proposal_draft(
    body: ProposalDraftRequest,
    owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Create a proposal draft; never submit it to a marketplace."""
    if not owner_id:
        raise HTTPException(status_code=401, detail="Authentication required")
    try:
        return draft_proposal(
            body.description,
            freelancer_name=body.freelancer_name,
            relevant_experience=body.relevant_experience,
            approach=body.approach,
            price_quote=body.price_quote,
            timeline=body.timeline,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
