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
    """Find a usable stock image; try focused alternatives and log safe diagnostics."""
    import logging
    logger = logging.getLogger(__name__)
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        logger.warning("Pexels image search skipped: PEXELS_API_KEY is not set in this service.")
        return None

    # Pexels search is keyword-based. A long product title plus niche can be too
    # restrictive, so try a small set of broader, still-relevant queries.
    normalized = " ".join(query.replace("-", " ").split())
    queries = [normalized]
    for candidate in ("small business automation", "business technology workspace", "small business owner laptop"):
        if candidate.casefold() not in normalized.casefold():
            queries.append(candidate)

    for search_query in queries[:4]:
        try:
            response = httpx.get(
                "https://api.pexels.com/v1/search",
                params={"query": search_query, "orientation": "portrait", "per_page": 5},
                headers={"Authorization": api_key},
                timeout=10.0,
            )
            if response.status_code in {401, 403}:
                logger.error("Pexels image search authentication/permission failed (HTTP %s). Check PEXELS_API_KEY in Render.", response.status_code)
                return None
            if response.status_code == 429:
                logger.warning("Pexels image search rate-limited (HTTP 429).")
                return None
            response.raise_for_status()
            payload = response.json()
            photos = payload.get("photos", [])
            for photo in photos if isinstance(photos, list) else []:
                src = photo.get("src", {}) if isinstance(photo, dict) else {}
                image_url = _first(src, "large", "large2x", "portrait", "original") if isinstance(src, dict) else None
                if _public_http_url(image_url):
                    logger.info("Pexels image search succeeded for query %r.", search_query)
                    return image_url
            logger.info("Pexels image search returned no photos for query %r.", search_query)
        except httpx.HTTPStatusError as exc:
            logger.warning("Pexels image search failed with HTTP %s.", exc.response.status_code)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Pexels image search request failed (%s).", type(exc).__name__)
    return None

def _vendor_product_image(product_url: str, product_name: str = "") -> str | None:
    """Return vendor artwork only when its metadata is relevant to the requested product."""
    from html.parser import HTMLParser
    from urllib.parse import urlparse

    parsed = urlparse(product_url)
    if parsed.scheme != "https" or parsed.hostname != "marketplace.iotsystemsgrowth.com":
        return None

    stop_words = {"small", "business", "and", "the", "for", "with", "toolkit", "tool", "tools", "pro", "ai", "automation", "automations", "system", "systems", "growth", "solution", "solutions", "resource", "resources", "digital"}
    terms = {
        word.casefold()
        for word in __import__("re").findall(r"[a-zA-Z0-9]+", product_name)
        if len(word) >= 4 and word.casefold() not in stop_words
    }
    # If the product title contains only generic category words, no vendor image can be\n    # confidently tied to this specific product. Force the stock-image fallback.\n    if not terms:\n        return None\n
    class MetadataParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.candidates: list[tuple[int, str]] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            values = {key.lower(): value for key, value in attrs if value}
            candidate = ""
            description = ""
            if tag.lower() == "meta":
                key = (values.get("property") or values.get("name") or "").lower()
                if key in {"og:image", "twitter:image"}:
                    candidate = values.get("content", "")
                    description = " ".join((values.get("alt", ""), values.get("title", "")))
            elif tag.lower() == "img":
                candidate = (
                    values.get("src") or values.get("data-src")
                    or values.get("data-lazy-src") or values.get("data-original") or ""
                )
                description = " ".join((
                    values.get("alt", ""), values.get("title", ""),
                    values.get("aria-label", ""), candidate,
                ))
            if not _public_http_url(candidate):
                return
            if not candidate.split("?")[0].lower().endswith((".jpg", ".jpeg", ".png", ".webp")):
                return
            haystack = description.casefold().replace("-", " ").replace("_", " ")
            matches = sum(1 for term in terms if term in haystack)
            # Do not blindly trust a page-wide Open Graph image: it may advertise
            # a different product. Require at least one product-specific signal.
            if matches:
                self.candidates.append((matches, candidate))

    try:
        from urllib.parse import urljoin

        current_url = product_url
        response = None
        for _ in range(4):
            current = urlparse(current_url)
            if current.scheme != "https" or current.hostname != "marketplace.iotsystemsgrowth.com":
                return None
            response = httpx.get(
                current_url,
                headers={"User-Agent": "AEA-Affiliate-Draft/1.0"},
                timeout=8.0,
                follow_redirects=False,
            )
            if response.status_code not in {301, 302, 303, 307, 308}:
                response.raise_for_status()
                break
            location = response.headers.get("location")
            if not location:
                return None
            current_url = urljoin(current_url, location)
        else:
            return None
        if response is None:
            return None
        parser = MetadataParser()
        parser.feed(response.text[:1_000_000])
        if not parser.candidates:
            return None
        parser.candidates.sort(key=lambda item: item[0], reverse=True)
        return parser.candidates[0][1]
    except (httpx.HTTPError, ValueError):
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
        # Surface a safe, actionable reason from the provider adapter instead of
        # collapsing missing credentials, auth errors, rate limits and model
        # errors into the same generic message. Never include credentials.
        provider_error = str(result.get("error") or "").strip()
        error_lower = provider_error.lower()
        if "missing groq_api_key" in error_lower:
            message = "GROQ_API_KEY is missing from the aea-core Render service environment."
        elif any(term in error_lower for term in ("401", "authentication", "invalid api key", "unauthorized")):
            message = "Groq rejected the API key (authentication failed). Verify the key in the aea-core Render service."
        elif any(term in error_lower for term in ("429", "rate limit", "quota")):
            message = "Groq rate limit or quota was reached. Check GroqCloud usage and limits."
        elif any(term in error_lower for term in ("model", "404", "decommissioned", "not found")):
            message = "The configured Groq model is unavailable. Set GROQ_MODEL to a currently supported model."
        elif "groq sdk not available" in error_lower:
            message = "The Groq Python SDK is unavailable in this deployment."
        elif provider_error:
            # Exception text from the SDK can contain request metadata; keep it
            # short and omit anything that looks like a credential.
            message = "Groq request failed: " + provider_error[:180]
        else:
            message = "A real LLM provider is not configured or failed. Configure GROQ_API_KEY and LLM_PROVIDER=groq."
        raise RuntimeError(message)
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
        # Marketplace entry IDs are distinct from product IDs. The API's
        # getMarketplaceEntry response exposes mainProductId; resolve it when the
        # listing record does not contain a product ID.
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
                        # Detail response is authoritative for product-specific fields.
                        entry = {**entry, **detail}
                except Digistore24MarketplaceError:
                    # Skip this candidate rather than confusing its entry ID for a product ID.
                    raw_product_id = None
        product_id = str(raw_product_id).strip() if raw_product_id is not None else None
        explicit_affiliate_url = _first(entry, "affiliate_url", "affiliate_link", "promotional_url", "promo_url", "tracking_url", "affiliate_tracking_url")
        # Keep an entry ID for display only when Digistore24 supplied a real tracking URL.
        if product_id is None and _public_http_url(explicit_affiliate_url):
            display_id = next((entry.get(key) for key in ("id", "entry_id", "marketplace_entry_id") if isinstance(entry.get(key), (str, int)) and not isinstance(entry.get(key), bool) and str(entry.get(key)).strip()), None)
            product_id = str(display_id).strip() if display_id is not None else None
        name = _first(entry, "headline", "name", "title")
        affiliate_url = explicit_affiliate_url
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
            "Digistore24 returned marketplace products but none contained a verified affiliate/tracking URL. AEA will not fabricate an affiliate link. "
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



def build_external_pinterest_draft(
    *, affiliate_url: str, niche: str = "AI tools and digital marketing",
    language: str = "English", product_name: str | None = None,
) -> dict[str, Any]:
    """Prepare a Pin from a user-supplied affiliate URL, without requiring Digistore24."""
    from urllib.parse import urlparse

    parsed = urlparse(affiliate_url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc or len(affiliate_url) > 2048:
        raise ValueError("Provide a valid affiliate destination URL beginning with https:// or http://.")
    destination = affiliate_url.strip()
    name = (product_name or "").strip() or niche.strip() or "Recommended resource"
    product = {
        "product_id": "user-supplied",
        "name": name[:200],
        "description": "Product details were not independently verified by AEA. Use only claims supplied by the publisher.",
        "category": niche,
        "affiliate_url": destination,
        "source": "User-supplied affiliate URL (not independently verified)",
    }
    # Prefer the product's own public image; only fetch metadata from the
    # explicitly allowlisted vendor host, then fall back to Pexels.
    image_url = _vendor_product_image(destination, name)
    if not _public_http_url(image_url):
        image_url = _pexels_image(name + " " + niche)
    if not _public_http_url(image_url):
        raise RuntimeError(
            "No public product image was found on the approved vendor page and Pexels returned no image. "
            "Check that the vendor page exposes og:image/twitter:image, or configure PEXELS_API_KEY."
        )
    copy = _generate_copy(product, niche=niche, language=language)
    return {
        "success": True,
        "product": {"id": "user-supplied", "name": name, "source": product["source"], "affiliate_url": destination},
        "draft": {**copy, "destination_url": destination, "image_url": image_url, "board_id": None},
        "requires_explicit_approval": True,
        "published": False,
        "note": "Prepared from a user-supplied destination URL. AEA has not independently verified the affiliate partnership or product claims; review before approval.",
    }
