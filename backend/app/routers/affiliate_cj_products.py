"""CJ Affiliate Product Feed API integration.

Uses the publisher's existing server-side Personal Access Token and Company ID.
The optional PID is supplied by an authenticated Hub caller when requesting
tracking links; it is never required for read-only product discovery.
"""
from __future__ import annotations

import json
import logging
import os
import re
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_current_user_id

router = APIRouter(prefix="/affiliate/cj", tags=["affiliate-cj"])
CJ_PRODUCT_FEED_URL = "https://ads.api.cj.com/query"
logger = logging.getLogger(__name__)


def _safe_cj_error(response: httpx.Response, token: str, company_id: str) -> str | None:
    """Extract a short provider diagnostic while redacting credentials and IDs."""
    message = ""
    try:
        payload = response.json()
        errors = payload.get("errors") if isinstance(payload, dict) else None
        if isinstance(errors, list):
            message = "; ".join(str(item.get("message") or "") for item in errors if isinstance(item, dict))
    except (ValueError, TypeError):
        message = ""
    if not message:
        message = getattr(response, "text", "") or ""
    for secret in (token, company_id):
        if secret:
            message = message.replace(secret, "[redacted]")
    message = re.sub(r"[^\x20-\x7e]", " ", message)
    message = " ".join(message.split())[:240]
    return message or None


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
    token, company_id = _credentials()
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
            summary = _safe_cj_error(response, token, company_id)
            logger.warning("CJ Product Feed API denied request (HTTP 403, provider_error=%s)", summary or "not-provided")
            detail = "CJ denied Product Feed API access for this publisher."
            if summary:
                detail += f" CJ response: {summary}"
            raise HTTPException(status_code=403, detail=detail)
        if response.status_code == 429:
            raise HTTPException(status_code=429, detail="CJ Product Feed API rate limit reached. Retry later.")
        if response.status_code >= 500:
            summary = _safe_cj_error(response, token, company_id)
            logger.warning(
                "CJ Product Feed API upstream failure (HTTP %s, provider_error=%s)",
                response.status_code,
                summary or "not-provided",
            )
            detail = "CJ Product Feed API is temporarily unavailable."
            if summary:
                detail += f" CJ response: {summary}"
            raise HTTPException(status_code=502, detail=detail)
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
    partner_filter = f", partnerIds: [{advertiser_id}]" if advertiser_id else ""
    # CJ's documented publisher query supports companyId and partnerIds here.
    query = (
        "{ productFeeds(companyId: " + company_id
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
    expected_count: int = Query(default=0, ge=0, le=1000000),
    pid: str | None = Query(default=None, min_length=1, max_length=32, pattern=r"^[0-9]+$"),
    limit: int = Query(default=50, ge=1, le=10000),
    offset: int = Query(default=1, ge=1),
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Search CJ products; when advertiser_id is supplied, scan provider pages
    until the requested advertiser's matching products are collected.

    Product requests are capped at 1,000 records per provider page to avoid
    oversized upstream responses. The advertiser filter is applied across pages,
    not just to one arbitrary page. Scanning is bounded and reports incomplete
    results rather than silently claiming the feed is exhausted.
    """
    _, company_id = _credentials()
    page_limit = min(limit, 1000)
    max_scan_pages = 100

    def build_query(include_tracking: bool, page_offset: int) -> str:
        link_field = f" linkCode(pid: {_gql_string(pid)}) {{ clickUrl }}" if include_tracking and pid else ""
        return (
            "query { products(companyId: " + _gql_string(company_id)
            + f", limit: {page_limit}, offset: {page_offset})"
            + " { totalCount count resultList { advertiserId catalogId id title description imageLink "
            "price { amount currency }" + link_field + " } } }"
        )

    tracking_links_included = bool(pid)
    tracking_link_error: str | None = None
    scanned_pages = 0
    scanned_records = 0
    matched: list[dict[str, Any]] = []
    provider_total = 0
    provider_exhausted = False
    scan_offset = 1
    needle = q.casefold().strip()
    target_end = offset + limit - 1
    target_count = expected_count

    while scanned_pages < (max_scan_pages if advertiser_id else 1):
        def fetch_page(include_tracking: bool) -> dict[str, Any]:
            return _request_cj(build_query(include_tracking, scan_offset)).get("products") or {}

        try:
            result = fetch_page(True)
        except HTTPException as exc:
            if exc.status_code != 403 or not pid:
                raise
            tracking_links_included = False
            tracking_link_error = "CJ denied tracking-link generation for the selected PID. Products were retried without affiliate links."
            result = fetch_page(False)

        if not isinstance(result, dict):
            raise HTTPException(status_code=502, detail="CJ response did not contain product results.")
        page_items = result.get("resultList") or []
        provider_total = int(result.get("totalCount") or provider_total or 0)
        scanned_pages += 1
        scanned_records += len(page_items)

        for item in page_items:
            if advertiser_id and str(item.get("advertiserId") or "") != advertiser_id:
                continue
            if needle and needle not in " ".join(str(item.get(key) or "") for key in ("title", "description")).casefold():
                continue
            matched.append(item)

        if not advertiser_id:
            provider_exhausted = len(page_items) == 0 or scan_offset + len(page_items) > provider_total
            break

        # If the feed summary supplied productCount, stop once that many products
        # have been found; otherwise scan until the provider reports exhaustion.
        if target_count and len([x for x in matched if advertiser_id and str(x.get("advertiserId") or "") == advertiser_id]) >= target_count and not needle:
            provider_exhausted = True
            break
        if not page_items or scan_offset + len(page_items) > provider_total:
            provider_exhausted = True
            break
        scan_offset += len(page_items)

    scan_incomplete = bool(advertiser_id and not provider_exhausted and scanned_pages >= max_scan_pages)
    page_items = matched[offset - 1: target_end]
    matched_total = target_count if target_count else (len(matched) if provider_exhausted else None)
    has_more = (matched_total is not None and target_end < matched_total) or (matched_total is None and len(matched) >= target_end)
    return {
        "network": "cj",
        "item_type": "product",
        "items": page_items,
        "count": len(page_items),
        "provider_count": scanned_records,
        "provider_total": provider_total,
        "total": matched_total,
        "limit": limit,
        "offset": offset,
        "next_offset": target_end + 1 if has_more else None,
        "tracking_links_included": tracking_links_included,
        "tracking_link_error": tracking_link_error,
        "read_only": True,
        "filter_applied_locally": bool(q.strip() or advertiser_id),
        "advertiser_filter_scanned_across_pages": bool(advertiser_id),
        "scanned_pages": scanned_pages,
        "scanned_records": scanned_records,
        "scan_incomplete": scan_incomplete,
    }

__all__ = ["router"]
