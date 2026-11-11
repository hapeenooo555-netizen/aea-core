"""CJ Promotional Properties API integration.

Read-only publisher PID discovery. The publisher CID and Personal Access
Token remain server-side; returned PIDs are for internal tracking-link use.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_current_user_id

router = APIRouter(prefix="/affiliate/cj", tags=["affiliate-cj"])
CJ_PROMOTIONAL_PROPERTIES_URL = "https://accounts.api.cj.com/graphql"
logger = logging.getLogger(__name__)
_ALLOWED_STATUSES = {"ACTIVE", "ARCHIVED", "TERMINATED"}


def _credentials() -> tuple[str, str]:
    token = os.getenv("CJ_PERSONAL_ACCESS_TOKEN", "").strip()
    publisher_id = os.getenv("CJ_COMPANY_ID", "").strip()
    if not token or not publisher_id:
        raise HTTPException(status_code=503, detail="CJ publisher credentials are not fully configured on AEA Core.")
    if not publisher_id.isdigit() or len(publisher_id) > 32:
        raise HTTPException(status_code=503, detail="CJ_COMPANY_ID must contain 1 to 32 digits.")
    return token, publisher_id


def _query_cj(query: str) -> dict[str, Any]:
    token, publisher_id = _credentials()
    try:
        response = httpx.post(
            CJ_PROMOTIONAL_PROPERTIES_URL,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json={"query": query},
            timeout=25.0,
        )
        if response.status_code == 401:
            raise HTTPException(status_code=401, detail="CJ rejected the configured Personal Access Token.")
        if response.status_code == 403:
            logger.warning("CJ Promotional Properties API denied publisher request (HTTP 403).")
            raise HTTPException(status_code=403, detail="CJ denied access to publisher promotional properties.")
        if response.status_code == 429:
            raise HTTPException(status_code=429, detail="CJ Promotional Properties API rate limit reached.")
        if response.status_code >= 500:
            raise HTTPException(status_code=502, detail="CJ Promotional Properties API is temporarily unavailable.")
        response.raise_for_status()
        payload = response.json()
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="CJ Promotional Properties API request timed out.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach CJ Promotional Properties API.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="CJ Promotional Properties API returned invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="CJ Promotional Properties API returned an unexpected response.")
    if payload.get("errors"):
        errors = payload.get("errors")
        message = " ".join(str(item.get("message") or "") for item in errors if isinstance(item, dict)).casefold()
        if "unauthor" in message or "invalid authorization" in message:
            raise HTTPException(status_code=401, detail="CJ rejected the configured Personal Access Token.")
        logger.warning("CJ Promotional Properties GraphQL query failed for publisher %s.", publisher_id)
        raise HTTPException(status_code=422, detail="CJ rejected the Promotional Properties GraphQL query.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="CJ response did not contain GraphQL data.")
    return data


@router.get("/promotional-properties")
def list_promotional_properties(
    search: str = Query(default="", max_length=120),
    status: str = Query(default="ACTIVE", min_length=1, max_length=16),
    limit: int = Query(default=100, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """List the authenticated publisher's PIDs without exposing credentials."""
    _, publisher_id = _credentials()
    normalized_status = status.upper()
    if normalized_status not in _ALLOWED_STATUSES:
        raise HTTPException(status_code=422, detail="Unsupported promotional property status.")
    search_arg = f", search: {json.dumps(search.strip())}" if search.strip() else ""
    query = (
        "{ promotionalProperties(publisherId: " + publisher_id
        + f", status: {normalized_status}, limit: {limit}, offset: {offset}"
        + search_arg + ") { totalCount resultList { "
        "id publisherId name description status isPrimary createdAt updatedAt "
        "tags { name } promotionalModels { type description isPrimary } "
        "propertyTypeDetails { type "
        "... on PromotionalPropertyWebsiteDetails { websiteUrl } "
        "... on PromotionalPropertySocialMediaDetails { socialMediaHandle socialMediaPlatform } "
        "... on PromotionalPropertyEmailDetails { emailAddress emailOpenRate emailMailingListSize } "
        "... on PromotionalPropertyMobileAppDetails { mobileAppName mobileAppDownloadInformation { mobileAppPlatform mobileAppDownloadLink } } "
        "... on PromotionalPropertyOtherDetails { otherDescription } "
        "... on PromotionalPropertyBrowserExtensionDetails { browserExtensionName browserExtensionDescription } "
        "... on PromotionalPropertyPaidSearchDetails { paidSearchEngineDescription } "
        "... on PromotionalPropertyPaidDisplayAdsDetails { paidDisplayAdsDescription } "
        "... on PromotionalPropertyServicesAndToolsDetails { servicesAndToolsMarketingSiteUrl } "
        "} } } }"
    )
    result = _query_cj(query).get("promotionalProperties")
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="CJ response did not contain promotional properties.")
    items = result.get("resultList") or []
    return {
        "network": "cj",
        "item_type": "promotional_property",
        "items": items,
        "count": len(items),
        "total": result.get("totalCount", len(items)),
        "limit": limit,
        "offset": offset,
        "next_offset": offset + limit if offset + limit < result.get("totalCount", 0) else None,
        "read_only": True,
    }


__all__ = ["router"]
