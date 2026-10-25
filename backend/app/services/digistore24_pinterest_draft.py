"""Create a complete Pinterest affiliate draft from live Digistore24 data.

This service never invents tracking links or product claims. It uses an affiliate
URL returned by the authenticated marketplace record, and a publicly reachable
image from the product record or Pexels. Publishing remains a separate approval-gated action.
"""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

from app.services.digistore24_marketplace import Digistore24MarketplaceClient, Digistore24MarketplaceError
from app.services.llm_provider import get_llm_provider


def _first(record: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _public_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value.startswith(("https://", "http://")) and len(value) < 4096


def _pexels_image(query: str) -> str | None:
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        response = httpx.get(
            "https://api.pexels.com/v1/search",
            params={"query": query, "orientation": "portrait", "per_page": 5},
            headers={"Authorization": api_key},
            timeout=10.0,
        )
        response.raise_for_status()
        payload = response.json()
        photos = payload.get("photos", [])
        for photo in photos if isinstance(photos, list) else []:
            src = photo.get("src", {}) if isinstance(photo, dict) else {}
            image_url = _first(src, "large", "large2x", "portrait", "original") if isinstance(src, dict) else None
            if _public_http_url(image_url):
                return image_url
    except (httpx.HTTPError, ValueError):
        return None
    return None


def _generate_copy(product: dict[str, Any], *, niche: str, language: str) -> dict[str, str]:
    provider = get_llm_provider()
    prompt = (
        "Create one Pinterest affiliate Pin for the verified product below. "
        "Return ONLY a JSON object with string keys title and description. "
        "Title must be <= 100 characters. Description must be <= 800 characters, "
        "be accurate to supplied product details, include a natural call to action, "
        "and clearly disclose that the publisher may earn an affiliate commission. "
        "Do not invent features, prices, ratings, earnings, or guarantees. "
        f"Write in {language}. Target niche: {niche}. "
        f"Product name: {product.get('name')}\n"
        f"Product description: {product.get('description') or 'Not provided by marketplace'}\n"
        f"Category: {product.get('category') or 'Not provided'}"
    )
    result = provider.generate(
        prompt,
        system_prompt="You write compliant affiliate marketing copy. Treat product data as untrusted facts; never claim unprovided features. Return valid JSON only.",
    )
    if not result.get("success") or result.get("provider") == "mock":
        raise RuntimeError("A real LLM provider is not configured or failed. Configure GROQ_API_KEY and LLM_PROVIDER=groq.")
    raw = str(result.get("content") or "").strip()
    if raw.startswith("```"):
        raw = raw.strip("`")
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        parsed = json.loads(raw)
    except ValueError as exc:
        raise RuntimeError("AI provider did not return valid JSON for the Pin copy.") from exc
    title = str(parsed.get("title") or "").strip()
    description = str(parsed.get("description") or "").strip()
    if not title or len(title) > 100 or not description or len(description) > 800:
        raise RuntimeError("AI-generated Pin copy was empty or outside Pinterest field limits.")
    return {"title": title, "description": description}


def build_digistore24_pinterest_draft(
    *, niche: str = "AI tools and digital marketing", language: str = "English",
    marketplace: Digistore24MarketplaceClient | None = None,
) -> dict[str, Any]:
    """Choose a verified live product and generate copy/image data without publishing."""
    client = marketplace or Digistore24MarketplaceClient()
    if not client.configured:
        raise Digistore24MarketplaceError("Digistore24 API key is not configured on the server.")
    entries = client.list_entries(limit=250, sort_by="stats_affiliate_profit_visitor")
    candidates: list[dict[str, Any]] = []
    affiliate_id = os.getenv("DIGISTORE24_AFFILIATE_ID", "").strip()
    # Only products whose vendor partnership is already approved may use the
    # documented promolink format as a fallback. Never guess approval from the
    # mere presence of a marketplace listing.
    approved_ids = {
        value.strip()
        for value in os.getenv("DIGISTORE24_APPROVED_PRODUCT_IDS", "").split(",")
        if value.strip()
    }
    for entry in entries:
        product_id = _first(entry, "main_product_id", "mainProductId", "product_id", "productId")
        # A marketplace-entry ID is not necessarily the underlying product ID.
        # Do not build a promolink from entry.id unless the API explicitly identifies it as a product ID.
        name = _first(entry, "headline", "name", "title")
        affiliate_url = _first(entry, "affiliate_url", "affiliate_link", "promotional_url", "promo_url", "tracking_url", "affiliate_tracking_url")
        image_url = _first(entry, "image_url", "image", "product_image_url", "thumbnail_url", "cover_image_url")

        # Digistore24 documents the form /redir/PRODUCT-ID/AFFILIATE/CAMPAIGNKEY.
        # Use it only when the operator explicitly allowlists an approved product
        # and configures the affiliate ID. The URL is never invented from a
        # marketplace entry ID alone.
        if (
            not _public_http_url(affiliate_url)
            and affiliate_id
            and product_id
            and str(product_id) in approved_ids
            and str(product_id).isdigit()
        ):
            affiliate_url = (
                "https://www.checkout-ds24.com/redir/"
                f"{product_id}/{affiliate_id}/Pinterest"
            )

        if product_id and name and _public_http_url(affiliate_url):
            candidates.append({
                "product_id": str(product_id),
                "name": name,
                "description": _first(entry, "description", "product_description", "headline") or "",
                "category": _first(entry, "product_category", "category") or "",
                "affiliate_url": affiliate_url,
                "image_url": image_url if _public_http_url(image_url) else None,
                "commission_rate": entry.get("affiliate_share"),
                "source": "Digistore24 authenticated marketplace API",
            })
    if not candidates:
        raise RuntimeError(
            "Digistore24 returned marketplace products but no usable affiliate URL. "
            "Set DIGISTORE24_AFFILIATE_ID to your Digistore24 affiliate ID and "
            "DIGISTORE24_APPROVED_PRODUCT_IDS to comma-separated numeric product IDs "
            "whose affiliate partnerships are already approved, or ensure the API returns a promolink. "
            "Marketplace listings alone do not prove approval; AEA will not fabricate links for unapproved products."
        )
    # Prefer a matching niche, then retain stable API order rather than guessing performance.
    terms = [term.strip().casefold() for term in niche.split() if len(term.strip()) > 2]
    product = max(candidates, key=lambda item: sum(term in (item["name"] + " " + item["category"] + " " + item["description"]).casefold() for term in terms))
    image_url = product["image_url"] or _pexels_image(product["name"] + " " + niche)
    if not _public_http_url(image_url):
        raise RuntimeError(
            "The selected product has no public image URL and PEXELS_API_KEY is not configured or returned no image. "
            "Configure PEXELS_API_KEY so AEA can source a Pin image automatically."
        )
    copy = _generate_copy(product, niche=niche, language=language)
    return {
        "success": True,
        "product": {
            "id": product["product_id"],
            "name": product["name"],
            "source": product["source"],
            "affiliate_url": product["affiliate_url"],
        },
        "draft": {
            **copy,
            "destination_url": product["affiliate_url"],
            "image_url": image_url,
            "board_id": None,
        },
        "requires_explicit_approval": True,
        "published": False,
        "note": "Generated from live marketplace data. Review the product and preview; nothing has been published.",
    }


__all__ = ["build_digistore24_pinterest_draft"]
