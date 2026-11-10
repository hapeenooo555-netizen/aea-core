"""CJ Affiliate Product Feed API integration.

Uses the publisher's existing server-side Personal Access Token and Company ID.
The optional PID is supplied by an authenticated Hub caller when requesting
tracking links; it is never required for read-only product discovery.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_current_user_id

router = APIRouter(prefix="/affiliate/cj", tags=["affiliate-cj"])
CJ_PRODUCT_FEED_URL = "https://ads.api.cj.com/query"


def _credentials() -> tuple[str, str]:
    token = os.getenv("CJ_PERSONAL_ACCESS_TOKEN", "").strip()
    company_id = os.getenv("CJ_COMPANY_ID", "").strip()
    if not token or not company_id:
        raise HTTPException(
            status_code=503,
            detail="CJ publisher credentials are not fully configured on AEA Core.",
        )
    if not company_id.isdigit() or len(company_id) > 32:
        raise HTTPException(
            status_code=503,
            detail="CJ_COMPANY_ID must contain 1 to 32 digits.",
        )
    return token, company_id


def _request_cj(query: str) -> dict[str, Any]:
    token, _ = _credentials()
    try:
        response = httpx.post(
            CJ_PRODUCT_FEED_URL,
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
            raise HTTPException(status_code=403, detail="CJ denied Product Feed API access for this publisher.")
        if response.status_code == 429:
            raise HTTPException(status_code=429, detail="CJ Product Feed API rate limit reached. Retry later.")
        if response.status_code >= 500:
            raise HTTPException(status_code=502, detail="CJ Product Feed API is temporarily unavailable.")
        response.raise_for_status()
        payload = response.json()
    except HTTPException:
        raise
    except httpx.TimeoutException as exc:
        raise HTTPException(status_code=504, detail="CJ Product Feed API request timed out.") from exc
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail="Could not reach CJ Product Feed API.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=502, detail="CJ Product Feed API returned invalid JSON.") from exc

    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="CJ Product Feed API returned an unexpected response.")
    if payload.get("errors"):
        # Do not relay raw provider messages: they may contain request details.
        raise HTTPException(status_code=422, detail="CJ rejected the Product Feed GraphQL query or its permissions.")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise HTTPException(status_code=502, detail="CJ response did not contain GraphQL data.")
    return data


def _gql_string(value: str) -> str:
    return json.dumps(value)


@router.get("/product-feeds")
def list_cj_product_feeds(
    advertiser_id: str | None = Query(default=None, min_length=1, max_length=32, pattern=r"^[0-9]+$"),
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """List feed summaries for all accessible advertisers or one advertiser."""
    _, company_id = _credentials()
    partner_filter = f', partnerIds: [{_gql_string(advertiser_id)}]' if advertiser_id else ""
    # CJ's documented publisher query supports companyId and partnerIds here.
    query = (
        "{ productFeeds(companyId: " + _gql_string(company_id)
        + partner_filter + ") { totalCount count resultList { adId feedName advertiserId "
        "productCount advertiserCountry lastUpdated advertiserName language currency "
        "sourceFeedType } } }"
    )
    result = _request_cj(query).get("productFeeds")
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="CJ response did not contain product feed summaries.")
    items = result.get("resultList") or []
    return {
        "network": "cj",
        "item_type": "product_feed",
        "items": items,
        "count": result.get("count", len(items)),
        "total": result.get("totalCount", len(items)),
        "read_only": True,
    }


@router.get("/products")
def search_cj_products(
    q: str = Query(default="", max_length=120),
    advertiser_id: str | None = Query(default=None, min_length=1, max_length=32, pattern=r"^[0-9]+$"),
    pid: str | None = Query(default=None, min_length=1, max_length=32, pattern=r"^[0-9]+$"),
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Search CJ shopping products and optionally return publisher tracking links.

    CJ returns up to its documented default product page size. Filtering by
    advertiser and keywords is applied to the returned page; no undocumented
    pagination arguments are sent.
    """
    _, company_id = _credentials()
    link_field = f" linkCode(pid: {_gql_string(pid)}) {{ clickUrl }}" if pid else ""
    query = (
        "query { products(companyId: " + _gql_string(company_id)
        + ") { totalCount resultList { advertiserId catalogId id title description "
        "price { amount currency }" + link_field + " } } }"
    )
    result = _request_cj(query).get("products")
    if not isinstance(result, dict):
        raise HTTPException(status_code=502, detail="CJ response did not contain product results.")
    items = result.get("resultList") or []
    needle = q.casefold().strip()
    if needle:
        items = [
            item for item in items
            if needle in " ".join(str(item.get(key) or "") for key in ("title", "description")).casefold()
        ]
    if advertiser_id:
        items = [item for item in items if str(item.get("advertiserId") or "") == advertiser_id]
    return {
        "network": "cj",
        "item_type": "product",
        "items": items,
        "count": len(items),
        "total": result.get("totalCount", len(items)),
        "tracking_links_included": bool(pid),
        "read_only": True,
        "filter_applied_locally": bool(q.strip() or advertiser_id),
    }

__all__ = ["router"]
