"""Create Pinterest drafts from user-supplied URLs and affiliate marketplace records."""
from __future__ import annotations

import json
import os
from typing import Any

import httpx

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
    """Find a relevant stock image and reject unrelated AI-chat/interface imagery."""
    import logging

    logger = logging.getLogger(__name__)
    api_key = os.getenv("PEXELS_API_KEY", "").strip()
    if not api_key:
        logger.warning("Pexels image search skipped: PEXELS_API_KEY is not configured.")
        return None

    normalized = " ".join(query.replace("-", " ").split())
    queries = [normalized]
    for candidate in (
        "small business owner working at desk laptop",
        "small business office workspace laptop notebook",
        "entrepreneur working on laptop at desk",
    ):
        if candidate.casefold() not in normalized.casefold():
            queries.append(candidate)

    excluded_signals = (
        "deepseek", "chatbot interface", "chatgpt interface", "ai chat interface",
        "chat interface", "robot", "robotic", "humanoid", "android",
    )
    relevance_signals = (
        "business", "office", "workspace", "laptop", "desk",
        "entrepreneur", "working", "small business",
    )
    for search_query in queries[:4]:
        try:
            response = httpx.get(
                "https://api.pexels.com/v1/search",
                params={"query": search_query, "orientation": "portrait", "per_page": 10},
                headers={"Authorization": api_key},
                timeout=10.0,
            )
            if response.status_code in {401, 403}:
                logger.error("Pexels image search authentication/permission failed (HTTP %s).", response.status_code)
                return None
            if response.status_code == 429:
                logger.warning("Pexels image search rate-limited (HTTP 429).")
                return None
            response.raise_for_status()
            payload = response.json()
            photos = payload.get("photos", [])
            ranked: list[tuple[int, str]] = []
            for photo in photos if isinstance(photos, list) else []:
                if not isinstance(photo, dict):
                    continue
                src = photo.get("src", {})
                image_url = _first(src, "large", "large2x", "portrait", "original") if isinstance(src, dict) else None
                if not _public_http_url(image_url):
                    continue
                alt = " ".join(str(photo.get("alt") or "").casefold().split())
                if any(signal in alt for signal in excluded_signals):
                    continue
                score = sum(signal in alt for signal in relevance_signals)
                # Never accept a generic technology/AI image just because Pexels
                # returned a valid URL. Require a business/workspace relevance signal.
                if score == 0:
                    continue
                ranked.append((score, image_url))
            if ranked:
                ranked.sort(key=lambda item: item[0], reverse=True)
                logger.info("Pexels returned a relevant candidate for query %r (score %s).", search_query, ranked[0][0])
                return ranked[0][1]
            logger.info("Pexels returned no acceptable photos for query %r.", search_query)
        except httpx.HTTPStatusError as exc:
            logger.warning("Pexels image search failed with HTTP %s.", exc.response.status_code)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("Pexels image search request failed (%s).", type(exc).__name__)
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


def build_external_pinterest_draft(
    *, affiliate_url: str, niche: str = "AI tools and digital marketing",
    language: str = "English", product_name: str | None = None,
    product_description: str | None = None,
) -> dict[str, Any]:
    """Create a reviewable Pinterest draft from user-confirmed product facts.

    AEA does not scrape arbitrary affiliate destinations. This avoids SSRF and
    prevents provider-specific marketplace assumptions from leaking into the
    generic URL workflow.
    """
    from urllib.parse import urlparse

    destination = affiliate_url.strip()
    parsed = urlparse(destination)
    if (
        parsed.scheme != "https" or not parsed.hostname
        or parsed.username or parsed.password
        or parsed.port not in (None, 443)
        or parsed.hostname.casefold() in {"localhost", "localhost.localdomain"}
        or len(destination) > 2048
    ):
        raise ValueError("Provide a valid public HTTPS affiliate URL.")
    name = (product_name or "").strip()[:200]
    description = (product_description or "").strip()[:3000]
    if not name or not description:
        raise ValueError("Provide the product name and factual description before generating a Pin draft.")
    image_url = _pexels_image(name + " " + description[:300])
    if not _public_http_url(image_url) or not image_url.startswith("https://"):
        raise RuntimeError("AEA could not find a relevant product image. No Pin was generated or published.")
    product = {
        "product_id": "user-supplied",
        "name": name,
        "description": description,
        "category": niche,
        "affiliate_url": destination,
        "source": "Product facts supplied by user; affiliate URL supplied by user",
    }
    copy = _generate_copy(product, niche=niche, language=language)
    return {
        "success": True,
        "product": {"id": "user-supplied", "name": name, "source": product["source"], "affiliate_url": destination},
        "draft": {**copy, "destination_url": destination, "image_url": image_url, "image_source": "pexels_relevant_search", "board_id": None},
        "requires_explicit_approval": True,
        "published": False,
        "note": "Draft created from user-supplied product facts and affiliate URL. Review the destination, image and claims before approval. Nothing has been published.",
    }


def build_external_pinterest_drafts(
    *, affiliate_url: str, niche: str = "AI tools and digital marketing",
    language: str = "English", product_name: str | None = None,
    product_description: str | None = None,
) -> dict[str, Any]:
    """Generate three copy options through AEA Core while resolving product artwork once."""
    base = build_external_pinterest_draft(
        affiliate_url=affiliate_url, niche=niche, language=language,
        product_name=product_name, product_description=product_description,
    )
    product = {
        "product_id": "user-supplied",
        "name": (product_name or "").strip() or niche.strip() or "Recommended resource",
        "description": (product_description or "").strip()[:3000]
            or "Product details were not independently verified by AEA. Use only claims supplied by the publisher.",
        "category": niche,
        "affiliate_url": affiliate_url.strip(),
        "source": "User-supplied affiliate URL (not independently verified)",
    }
    angle_specs = (
        ("productivity", "practical productivity benefits and streamlined workflows", "Boost Productivity with"),
        ("time_saving", "saving time by automating repetitive daily tasks", "Save Time with"),
        ("business_growth", "helping small businesses operate smarter and focus on growth", "Grow Your Business with"),
    )
    pins: list[dict[str, str]] = []
    for angle_key, angle_prompt, _title_prefix in angle_specs:
        pins.append(_generate_copy(product, niche=f"{niche}; {angle_key}: {angle_prompt}", language=language))

    # Enforce three distinct benefit-led headlines even when the LLM returns
    # near-duplicates (e.g. articles such as "the" / "an" differ only slightly).
    # Descriptions remain model-generated for each requested creative angle.
    safe_name = (product["name"] or "AI Automation Toolkit").strip()
    for index, pin in enumerate(pins):
        pin["title"] = f"{angle_specs[index][2]} {safe_name}"[:100].rstrip()
    return {
        "success": True,
        "product": base["product"],
        "pins": pins,
        "image_url": base["draft"]["image_url"],
        "disclosure_reminder": "Clearly disclose your affiliate relationship in the Pin. You may earn a commission if someone purchases through your link.",
        "requires_explicit_approval": True,
        "published": False,
        "note": "Generated by AEA Core. Review and edit each option; nothing has been published.",
    }


def build_marketplace_pinterest_draft(
    *, affiliate_url: str, product_url: str, product_name: str,
    product_description: str = "", product_image_url: str,
    provider: str, niche: str = "AI tools and business automation",
    language: str = "English",
) -> dict[str, Any]:
    """Create a draft from a marketplace record and user-supplied approved tracking URL.

    Product metadata and artwork are taken from the authenticated marketplace
    response. The affiliate destination is supplied by the user after approval;
    AEA does not claim to verify vendor partnership status when the provider API
    does not expose that status.
    """
    from urllib.parse import urlparse

    def valid_https_url(value: str) -> bool:
        parsed = urlparse(value)
        return (
            parsed.scheme == "https" and bool(parsed.hostname)
            and not parsed.username and not parsed.password
            and parsed.port in (None, 443)
            and parsed.hostname.casefold() not in {"localhost", "localhost.localdomain"}
            and len(value) <= 2048
        )

    destination = affiliate_url.strip()
    source_url = product_url.strip()
    image_url = product_image_url.strip()
    name = product_name.strip()[:200]
    description = product_description.strip()[:3000]
    if not valid_https_url(destination):
        raise ValueError("Provide the approved affiliate tracking URL using HTTPS.")
    if not valid_https_url(source_url):
        raise ValueError("The selected product page must be a valid HTTPS URL.")
    if not valid_https_url(image_url):
        raise ValueError("The marketplace did not provide a valid HTTPS product image.")
    if not name:
        raise ValueError("The selected marketplace product must have a name.")

    product = {
        "product_id": "marketplace-selected",
        "name": name,
        "description": description or "Product details supplied by the selected affiliate marketplace.",
        "category": niche,
        "affiliate_url": destination,
        "source": f"{provider.strip() or 'Affiliate network'} marketplace record; affiliate URL supplied by user after provider approval",
    }
    copy = _generate_copy(product, niche=niche, language=language)
    return {
        "success": True,
        "product": {
            "id": "marketplace-selected", "name": name,
            "source": product["source"], "affiliate_url": destination,
            "product_url": source_url, "provider": provider,
            "promotion_status": "user_confirmed_not_provider_verified",
        },
        "draft": {
            **copy, "destination_url": destination, "image_url": image_url,
            "image_source": "affiliate_marketplace_product_record", "board_id": None,
        },
        "requires_explicit_approval": True,
        "published": False,
        "note": "Draft created from the selected marketplace product. The affiliate tracking URL was supplied by you after applying; AEA has not independently verified the provider's approval. Review the link and content before approval.",
    }
