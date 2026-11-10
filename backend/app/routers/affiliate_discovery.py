"""Authenticated, read-only affiliate network discovery adapters.

Provider credentials remain server-side. Responses are normalized and never
include API keys or Awin feed download URLs (which embed the data-feed key).
"""
from __future__ import annotations

import csv
import io
import os
import xml.etree.ElementTree as ET
from typing import Any
import logging

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_current_user_id

router = APIRouter(prefix="/affiliate/discovery", tags=["affiliate-discovery"])
logger = logging.getLogger(__name__)

CJ_LOOKUP_URL = "https://advertiser-lookup.api.cj.com/v2/advertiser-lookup"
AWIN_FEED_LIST_URL = "https://ui.awin.com/productdata-darwin-download/publisher/{publisher_id}/{feed_key}/1/feedList"
PARTNERSTACK_PROGRAMS_URL = "https://api.partnerstack.com/api/v2/marketplace/programs"


def _cj_error_summary(response: httpx.Response, token: str = "", cid: str = "") -> str | None:
    """Extract a short CJ XML error message, redacting credentials and publisher CID."""
    try:
        root = ET.fromstring(response.text)
    except ET.ParseError:
        return None
    for node in root.iter():
        if node.tag.rsplit("}", 1)[-1].casefold() in {"message", "error", "error-message", "description"} and node.text:
            summary = " ".join(node.text.split())
            for secret in (token, cid):
                if secret:
                    summary = summary.replace(secret, "[redacted]")
            summary = "".join(ch for ch in summary if ch.isprintable())[:240]
            return summary or None
    return None


def _safe_request_error(provider: str, response: httpx.Response | None = None, *, token: str = "", cid: str = "") -> HTTPException:
    if response is not None and response.status_code in (401, 403):
        return HTTPException(status_code=response.status_code, detail=f"{provider} rejected the configured credentials or denied this resource.")
    if response is not None and response.status_code == 429:
        return HTTPException(status_code=429, detail=f"{provider} rate limit reached. Retry later.")
    if response is not None:
        # Surface the upstream status without echoing provider bodies, request headers, or credentials.
        summary = _cj_error_summary(response, token, cid) if provider == "CJ" else None
        logger.warning("%s advertiser discovery returned upstream HTTP %s (content-type=%s, provider_error=%s)", provider, response.status_code, response.headers.get("content-type", "unknown"), summary or "not-provided")
        detail = f"{provider} advertiser discovery returned upstream HTTP {response.status_code}."
        detail += f" CJ says: {summary}" if summary else " Check the publisher CID, API access, and provider status."
        return HTTPException(status_code=502, detail=detail)
    logger.warning("%s advertiser discovery failed before receiving an HTTP response", provider)
    return HTTPException(status_code=502, detail=f"Could not reach {provider} advertiser discovery API. Check provider availability and retry.")


def _cj_advertisers(query: str, limit: int) -> dict[str, Any]:
    """Discover feeds already accessible to this CJ publisher via Product Feed GraphQL.

    The separate Advertiser Lookup REST API may deny a publisher CID even when
    the Product Feed API is available. Use the same token/company credentials
    as the Hub's CJ catalog and expose only feeds the publisher can already access.
    """
    from app.routers.affiliate_cj_products import _gql_string, _request_cj

    # Product Feed API accepts companyId and returns only publisher-accessible feeds.
    gql = (
        "{ productFeeds(companyId: " + os.getenv("CJ_COMPANY_ID", "").strip()
        + ") { totalCount count resultList { adId feedName advertiserId "
        "productCount advertiserCountry lastUpdated advertiserName language currency "
        "sourceFeedType } } }"
    )
    data = _request_cj(gql)
    feeds = data.get("productFeeds")
    if not isinstance(feeds, dict):
        raise HTTPException(status_code=502, detail="CJ Product Feed API did not return product feed summaries.")
    needle = query.casefold().strip()
    items: list[dict[str, Any]] = []
    for row in feeds.get("resultList") or []:
        if not isinstance(row, dict):
            continue
        name = str(row.get("advertiserName") or "")
        feed_name = str(row.get("feedName") or "")
        if needle and needle not in f"{name} {feed_name} {row.get('sourceFeedType') or ''}".casefold():
            continue
        items.append({
            "id": str(row.get("advertiserId") or row.get("adId") or ""),
            "name": name or feed_name or "CJ advertiser product feed",
            "website": None,
            "relationship_status": "publisher_accessible_feed",
            "account_status": None,
            "category": [],
            "feed_id": row.get("adId"),
            "feed_name": feed_name,
            "product_count": row.get("productCount"),
            "region": row.get("advertiserCountry"),
            "language": row.get("language"),
            "currency": row.get("currency"),
            "source": "cj",
            "item_type": "product_feed",
        })
        if len(items) >= limit:
            break
    return {
        "provider": "cj",
        "configured": True,
        "item_type": "product_feed",
        "total_matched": feeds.get("totalCount", len(items)),
        "items": items,
        "note": "CJ Advertiser Lookup is not used. Results are product feeds already accessible to this publisher account.",
    }


def _awin_feeds(query: str, limit: int) -> dict[str, Any]:
    publisher_id = os.getenv("AWIN_PUBLISHER_ID", "").strip()
    feed_key = os.getenv("AWIN_DATA_FEED_KEY", "").strip()
    if not publisher_id or not feed_key:
        return {"provider": "awin", "configured": False, "items": [], "message": "Awin Publisher ID and Data Feed Key are required. The Publisher API key is not a substitute."}
    url = AWIN_FEED_LIST_URL.format(publisher_id=publisher_id, feed_key=feed_key)
    try:
        response = httpx.get(url, headers={"Accept": "text/csv"}, timeout=30.0)
        response.raise_for_status()
        text = response.text.lstrip("\ufeff")
        reader = csv.DictReader(io.StringIO(text))
        items = []
        needle = query.casefold().strip()
        for row in reader:
            name = (row.get("Advertiser Name") or "").strip()
            vertical = (row.get("Vertical") or "").strip()
            if needle and needle not in f"{name} {vertical} {row.get('Primary Region', '')}".casefold():
                continue
            # Do not return URL: Awin embeds the feed key in that download URL.
            items.append({
                "id": (row.get("Advertiser ID") or "").strip(),
                "name": name,
                "region": (row.get("Primary Region") or "").strip(),
                "membership_status": (row.get("Membership Status") or "").strip(),
                "feed_id": (row.get("Feed ID") or "").strip(),
                "feed_name": (row.get("Feed Name") or "").strip(),
                "language": (row.get("Language") or "").strip(),
                "vertical": vertical,
                "product_count": (row.get("No of products") or "").strip(),
                "last_imported": (row.get("Last Imported") or "").strip(),
                "last_checked": (row.get("Last Checked") or "").strip(),
                "source": "awin",
                "item_type": "product_feed",
                "deep_links_available_in_feed": True,
            })
            if len(items) >= limit:
                break
    except httpx.HTTPStatusError as exc:
        raise _safe_request_error("Awin", exc.response) from exc
    except (httpx.HTTPError, csv.Error) as exc:
        raise HTTPException(status_code=502, detail="Awin feed list could not be downloaded or parsed.") from exc
    return {"provider": "awin", "configured": True, "items": items, "note": "Feed metadata only. Product rows are available by downloading an eligible feed through the backend."}


def _partnerstack_programs(query: str, category: str | None, limit: int) -> dict[str, Any]:
    api_key = os.getenv("PARTNERSTACK_API_KEY", "").strip()
    if not api_key:
        return {"provider": "partnerstack", "configured": False, "items": [], "message": "PartnerStack API key is not configured on AEA Core."}
    params: dict[str, Any] = {"limit": min(limit, 250)}
    if category:
        params["category"] = category[:80]
    try:
        response = httpx.get(PARTNERSTACK_PROGRAMS_URL, params=params, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"}, timeout=20.0)
        response.raise_for_status()
        payload = response.json()
    except httpx.HTTPStatusError as exc:
        raise _safe_request_error("PartnerStack", exc.response) from exc
    except (httpx.HTTPError, ValueError) as exc:
        raise HTTPException(status_code=502, detail="PartnerStack returned an unavailable or invalid program response.") from exc
    data = payload.get("data", []) if isinstance(payload, dict) else []
    if isinstance(data, dict):
        data = data.get("programs", data.get("items", []))
    if not isinstance(data, list):
        raise HTTPException(status_code=502, detail="PartnerStack program response had an unexpected format.")
    needle = query.casefold().strip()
    items = []
    for row in data:
        if not isinstance(row, dict):
            continue
        name = str(row.get("name") or row.get("company_name") or row.get("program_name") or "")
        description = str(row.get("description") or "")
        if needle and needle not in f"{name} {description}".casefold():
            continue
        items.append({
            "id": row.get("key") or row.get("company_key") or row.get("id"),
            "name": name,
            "description": description,
            "category": row.get("category"),
            "website": row.get("website") or row.get("url"),
            "source": "partnerstack",
            "item_type": "marketplace_program",
            "application_required": True,
        })
    return {"provider": "partnerstack", "configured": True, "items": items}


@router.get("")
def discover_affiliate_sources(
    q: str = Query(default="", max_length=120),
    category: str | None = Query(default=None, max_length=80),
    provider: str = Query(default="all", pattern="^(all|cj|awin|partnerstack)$"),
    limit: int = Query(default=25, ge=1, le=100),
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Search connected publisher catalogs. Read-only; does not apply or create links."""
    providers = ["cj", "awin", "partnerstack"] if provider == "all" else [provider]
    results = []
    for name in providers:
        if name == "cj":
            results.append(_cj_advertisers(q, limit))
        elif name == "awin":
            results.append(_awin_feeds(q, limit))
        else:
            results.append(_partnerstack_programs(q, category, limit))
    all_items = [item for result in results for item in result.get("items", [])]
    return {
        "query": q,
        "category": category,
        "providers": results,
        "items": all_items,
        "total": len(all_items),
        "read_only": True,
        "applications_submitted": False,
        "credentials_exposed": False,
    }


__all__ = ["router"]
