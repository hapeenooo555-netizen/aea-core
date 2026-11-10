"""CJ Affiliate credential readiness and publisher authentication checks.

The status endpoint only reports server-side configuration presence. A real
provider authentication check uses the Promotional Properties API and requires
the publisher CID; it never returns or logs the personal access token.
"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.dependencies import get_current_user_id

router = APIRouter(prefix="/affiliate/cj", tags=["affiliate-cj"])

CJ_PROMOTIONAL_PROPERTIES_URL = "https://accounts.api.cj.com/graphql"
CJ_TOKEN_ENV = "CJ_PERSONAL_ACCESS_TOKEN"


class CJVerifyRequest(BaseModel):
    # Optional for Hub callers: the publisher CID should normally come from
    # the server environment, never from browser configuration.
    company_id: str | None = Field(
        default=None, min_length=1, max_length=32, pattern=r"^[0-9]+$"
    )


@router.get("/connection/status")
def cj_connection_status(
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Report configuration readiness without claiming token validity."""
    token_present = bool(os.getenv(CJ_TOKEN_ENV, "").strip())
    cid_present = bool(os.getenv("CJ_COMPANY_ID", "").strip())
    return {
        "network": "cj",
        "credential_configured": token_present,
        "company_id_configured": cid_present,
        "authentication_verified": False,
        "status": (
            "ready_to_verify" if token_present and cid_present
            else "company_id_required" if token_present
            else "token_not_configured"
        ),
        "message": (
            "Credentials are configured; run the authenticated verification."
            if token_present and cid_present
            else "CJ Personal Access Token is present. Publisher CID is required "
                 "for a real authenticated API check."
            if token_present
            else "Set CJ_PERSONAL_ACCESS_TOKEN in the AEA Core server environment."
        ),
    }


@router.post("/connection/verify")
def verify_cj_connection(
    body: CJVerifyRequest | None = None,
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Verify the PAT against the publisher's own Promotional Properties query."""
    token = os.getenv(CJ_TOKEN_ENV, "").strip()
    # Prefer the server-side publisher CID. The optional body field remains
    # for backwards compatibility with existing authenticated clients.
    company_id = os.getenv("CJ_COMPANY_ID", "").strip() or ((body.company_id if body else None) or "").strip()
    if not company_id:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CJ_COMPANY_ID is not configured on AEA Core.",
        )
    if not company_id.isdigit() or len(company_id) > 32:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CJ_COMPANY_ID on AEA Core must contain 1 to 32 digits.",
        )
    if not token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="CJ Personal Access Token is not configured on AEA Core.",
        )

    query = (
        '{ promotionalProperties(publisherId: "' + company_id
        + '", status: ACTIVE, limit: 1, offset: 0) { totalCount } }'
    )
    try:
        response = httpx.post(
            CJ_PROMOTIONAL_PROPERTIES_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"query": query},
            timeout=15.0,
        )
    except httpx.TimeoutException as exc:
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="CJ API request timed out. Retry the verification.",
        ) from exc
    except httpx.RequestError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Could not reach CJ API.",
        ) from exc

    if response.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="CJ rejected the Personal Access Token. Check or rotate the token.",
        )
    if response.status_code == 403:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CJ authenticated the request but denied access to this API resource.",
        )
    if response.status_code >= 500:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="CJ API is temporarily unavailable.",
        )
    if response.status_code >= 400:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"CJ API returned HTTP {response.status_code}. Check the CID and API access.",
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="CJ API returned an invalid response.",
        ) from exc

    if payload.get("errors"):
        # GraphQL validation/permission errors are not treated as successful auth.
        messages = payload["errors"]
        safe_message = "CJ returned a GraphQL error. Verify the CID and publisher API access."
        if any("unauthor" in str(item).lower() or "forbidden" in str(item).lower()
               for item in messages):
            raise HTTPException(status_code=403, detail=safe_message)
        raise HTTPException(status_code=422, detail=safe_message)

    data = payload.get("data")
    properties = data.get("promotionalProperties") if isinstance(data, dict) else None
    if not isinstance(properties, dict) or "totalCount" not in properties:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="CJ response did not contain the expected publisher property result.",
        )

    return {
        "network": "cj",
        "authentication_verified": True,
        "status": "connected",
        "company_id_verified": True,
        "promotional_properties_found": properties["totalCount"],
        "message": "CJ accepted the Personal Access Token for this publisher account.",
    }


__all__ = ["router"]
