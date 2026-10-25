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
    # This allowlist is an operator assertion that vendor partnerships are approved.
    # It must never be populated from marketplace listings alone.
    approved_ids = {
        value.strip()
        for value in os.getenv("DIGISTORE24_APPROVED_PRODUCT_IDS", "").split(",")
        if value.strip()
    }
    for entry in entries:
        raw_product_id = next(
            (
                entry.get(key)
                for key in ("main_product_id", "mainProductId", "main_product", "product_id", "productId")
                if isinstance(entry.get(key), (str, int)) and not isinstance(entry.get(key), bool) and str(entry.get(key)).strip()
            ),
            None,
        )
        if raw_product_id is None:
            entry_id = next(
                (
                    entry.get(key)
                    for key in ("id", "entry_id", "marketplace_entry_id")
                    if isinstance(entry.get(key), (str, int)) and not isinstance(entry.get(key), bool) and str(entry.get(key)).strip()
                ),
                None,
            )
            detail_getter = getattr(client, "get_entry", None)
            if entry_id is not None and callable(detail_getter):
                try:
                    detail = detail_getter(entry_id)
                    raw_product_id = next(
                        (
                            detail.get(key)
                            for key in ("mainProductId", "main_product_id", "productId", "product_id")
                            if isinstance(detail.get(key), (str, int)) and not isinstance(detail.get(key), bool) and str(detail.get(key)).strip()
                        ),
                        None,
                    )
                    if raw_product_id is not None:
                        entry = {**entry, **detail}
                except Digistore24MarketplaceError:
                    raw_product_id = None
        product_id = str(raw_product_id).strip() if raw_product_id is not None else None
        explicit_affiliate_url = _first(entry, "affiliate_url", "affiliate_link", "promotional_url", "promo_url", "tracking_url", "affiliate_tracking_url", "promolink", "promoLink")
        if product_id is None and _public_http_url(explicit_affiliate_url):
            display_id = next((entry.get(key) for key in ("id", "entry_id", "marketplace_entry_id") if isinstance(entry.get(key), (str, int)) and not isinstance(entry.get(key), bool) and str(entry.get(key)).strip()), None)
            product_id = str(display_id).strip() if display_id is not None else None
        name = _first(entry, "headline", "name", "title")
        affiliate_url = explicit_affiliate_url
        image_url = _first(entry, "image_url", "image", "product_image_url", "thumbnail_url", "cover_image_url")

        # The documented URL shape does not itself prove partnership approval.
        # Only use the fallback when the operator has explicitly allowlisted an
        # approved product ID and configured their Digistore24 affiliate ID.
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
        if not affiliate_id:
            next_step = "Configure DIGISTORE24_AFFILIATE_ID with your Digistore24 affiliate ID in the Render service environment."
        elif not approved_ids:
            next_step = (
                "No approved product IDs are configured. In Digistore24 Affiliate View, open Marketplace and select "
                "a product, then choose Promote now. Wait until the partnership status is Approved (not Pending), "
                "then add that numeric product ID to DIGISTORE24_APPROVED_PRODUCT_IDS in Render and redeploy. "
                "Separate multiple IDs with commas. Do not add products that are merely listed or still pending."
            )
        else:
            next_step = (
                "Check that DIGISTORE24_APPROVED_PRODUCT_IDS contains the numeric product ID (not the marketplace "
                "entry ID), and that the vendor partnership is approved. If the API returns a promolink, AEA will use it."
            )
        raise RuntimeError(
            "AEA fetched Digistore24 marketplace listings, but none had an API-provided affiliate URL or a configured "
            "approved-product fallback. Digistore24 marketplace visibility does not grant affiliate permission. "
            + next_step
            + " AEA will not request or assume partnership approval unless Digistore24 exposes a documented API operation for it."
        )
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
