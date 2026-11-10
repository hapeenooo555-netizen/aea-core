"""CJ Product Feed API connection verification.

A successful verification must prove that the configured publisher token and
CID can read the Publisher Product Feed API. Credential presence alone is not
reported as a verified connection.
"""
from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.dependencies import get_current_user_id
from app.routers.affiliate_cj_products import _request_cj

router = APIRouter(prefix="/affiliate/cj", tags=["affiliate-cj"])
CJ_TOKEN_ENV = "CJ_PERSONAL_ACCESS_TOKEN"


class CJVerifyRequest(BaseModel):
    # Retained for API compatibility. The server-side CID is authoritative.
    company_id: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=r"^[0-9]+$"
    )


@router.get("/connection/status")
def cj_connection_status(
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Report configuration presence without claiming provider verification."""
    token_present = bool(os.getenv(CJ_TOKEN_ENV, "").strip())
    cid_present = bool(os.getenv("CJ_COMPANY_ID", "").strip())
    return {
        "network": "cj",
        "credential_configured": token_present,
        "company_id_configured": cid_present,
        "authentication_verified": False,
        "product_feed_api_verified": False,
        "status": (
            "ready_to_verify" if token_present and cid_present
            else "company_id_required" if token_present
            else "token_not_configured"
        ),
        "message": (
            "Credentials are configured; verify access to the Publisher Product Feed API."
            if token_present and cid_present
            else "CJ Personal Access Token is present. Publisher Company ID is required."
            if token_present
            else "Set CJ_PERSONAL_ACCESS_TOKEN in the AEA Core server environment."
        ),
    }


@router.post("/connection/verify")
def verify_cj_connection(
    body: CJVerifyRequest | None = None,
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Verify actual publisher access by querying product feed summaries."""
    del body  # CID must come from trusted server configuration, not the browser.
    token = os.getenv(CJ_TOKEN_ENV, "").strip()
    company_id = os.getenv("CJ_COMPANY_ID", "").strip()
    if not token:
        raise HTTPException(status_code=503, detail="CJ Personal Access Token is not configured on AEA Core.")
    if not company_id:
        raise HTTPException(status_code=503, detail="CJ_COMPANY_ID is not configured on AEA Core.")
    if not company_id.isdigit() or len(company_id) > 32:
        raise HTTPException(status_code=503, detail="CJ_COMPANY_ID on AEA Core must contain 1 to 32 digits.")

    # Publisher query: no partnerIds means CJ returns feeds across advertisers
    # accessible to this publisher, including joined and non-joined advertisers.
    query = (
        "{ productFeeds(companyId: " + _gql_string(company_id)
        + ") { totalCount count resultList { adId advertiserId } } }"
    )
    result = _request_cj(query).get("productFeeds")
    if not isinstance(result, dict) or not isinstance(result.get("totalCount"), (int, float)):
        raise HTTPException(
            status_code=502,
            detail="CJ Product Feed API returned an unexpected feed summary response.",
        )

    return {
        "network": "cj",
        "authentication_verified": True,
        "product_feed_api_verified": True,
        "status": "connected",
        "company_id_verified": True,
        "accessible_feed_count": result["totalCount"],
        "feeds_in_sample": result.get("count", len(result.get("resultList") or [])),
        "message": "CJ accepted the publisher credentials and returned Product Feed API data.",
    }


__all__ = ["router"]
