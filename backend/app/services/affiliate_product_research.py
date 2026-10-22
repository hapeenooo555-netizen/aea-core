"""Evidence-first affiliate product scoring.

This module ranks *provided marketplace records*; it does not scrape websites,
invent performance metrics, or claim to fetch live Digistore24 listings. Callers
must supply records obtained through an authorized marketplace/API integration.
All ranked items remain research candidates until a human approves promotion.
"""
from __future__ import annotations

from typing import Any, Iterable
from urllib.parse import urlparse


_REQUIRED_NUMERIC = ("commission_rate", "earnings_per_click", "conversion_rate", "cancellation_rate")


def _number(record: dict[str, Any], key: str) -> float | None:
    value = record.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _valid_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value.strip())
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def score_marketplace_product(
    product: dict[str, Any],
    *,
    target_niches: Iterable[str] = ("AI tools", "digital marketing", "software"),
) -> dict[str, Any]:
    """Return an auditable score for a marketplace record or a rejection reason.

    Expected numeric fields are percentage values for commission_rate,
    conversion_rate and cancellation_rate, and a non-negative currency amount
    for earnings_per_click. Scores are heuristic, not a revenue guarantee.
    """
    if not isinstance(product, dict):
        return {"eligible": False, "reason": "invalid_record"}
    product_id = product.get("product_id") or product.get("id")
    name = str(product.get("name") or product.get("title") or "").strip()
    source_url = product.get("source_url") or product.get("marketplace_url")
    if not product_id or not name:
        return {"eligible": False, "reason": "missing_product_identity"}
    if not _valid_http_url(source_url):
        return {"eligible": False, "reason": "missing_verifiable_source_url"}
    if product.get("verified") is not True:
        return {"eligible": False, "reason": "marketplace_record_not_verified"}
    missing = [key for key in _REQUIRED_NUMERIC if _number(product, key) is None]
    if missing:
        return {"eligible": False, "reason": "missing_performance_metrics", "missing": missing}

    commission = _number(product, "commission_rate")
    epc = _number(product, "earnings_per_click")
    conversion = _number(product, "conversion_rate")
    cancellation = _number(product, "cancellation_rate")
    if commission is None or commission > 100 or conversion is None or conversion > 100 or cancellation is None or cancellation > 100:
        return {"eligible": False, "reason": "metrics_out_of_range"}

    niche_text = " ".join(
        str(product.get(key) or "") for key in ("category", "description", "name", "title")
    ).casefold()
    matched_niches = sorted({
        str(niche).strip()
        for niche in target_niches
        if str(niche).strip() and str(niche).strip().casefold() in niche_text
    })
    # Bounded weighted score. Inputs are marketplace facts, not predictions.
    commission_score = min(commission, 100.0) * 0.20
    epc_score = min(epc / 2.0, 100.0) * 0.35
    conversion_score = min(conversion, 100.0) * 0.20
    cancellation_score = (100.0 - cancellation) * 0.15
    niche_score = 10.0 if matched_niches else 0.0
    score = round(commission_score + epc_score + conversion_score + cancellation_score + niche_score, 2)
    return {
        "eligible": True,
        "product_id": str(product_id),
        "name": name,
        "source_url": source_url.strip(),
        "score": score,
        "matched_niches": matched_niches,
        "metrics": {
            "commission_rate": commission,
            "earnings_per_click": epc,
            "conversion_rate": conversion,
            "cancellation_rate": cancellation,
        },
        "score_explanation": "Heuristic ranking of supplied marketplace metrics and niche fit; not an earnings guarantee.",
        "requires_human_approval": True,
        "published": False,
    }


def rank_marketplace_products(
    products: Iterable[dict[str, Any]],
    *,
    target_niches: Iterable[str] = ("AI tools", "digital marketing", "software"),
    limit: int = 10,
) -> dict[str, Any]:
    """Rank eligible verified records; never manufacture fallback candidates."""
    if limit < 1:
        raise ValueError("limit must be at least 1")
    ranked = []
    rejected = []
    for product in products:
        result = score_marketplace_product(product, target_niches=target_niches)
        if result.get("eligible"):
            ranked.append(result)
        else:
            rejected.append({
                "product_id": (product.get("product_id") or product.get("id")) if isinstance(product, dict) else None,
                "reason": result.get("reason", "invalid_record"),
                **({"missing": result["missing"]} if "missing" in result else {}),
            })
    ranked.sort(key=lambda item: (-item["score"], item["name"].casefold(), item["product_id"]))
    return {
        "status": "ranked" if ranked else "needs_verified_catalog_data",
        "source_record_count": len(ranked) + len(rejected),
        "eligible_count": len(ranked),
        "rejected_count": len(rejected),
        "candidates": ranked[:limit],
        "rejected": rejected,
        "requires_human_approval_before_promotion": True,
        "published": False,
        "live_marketplace_fetch_performed": False,
    }


__all__ = ["rank_marketplace_products", "score_marketplace_product"]
