"""Offline normalization for approved affiliate data imports.

These functions process records already obtained through an authorized provider
feed/API. They do not fetch or scrape provider marketplaces and do not infer
affiliate approval from the presence of a product listing.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.services.affiliate_networks.catalog import AffiliateOffer


def _first(record: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = record.get(key)
        if value is not None and value != "":
            return value
    return None


def _text(value: Any) -> str | None:
    return str(value).strip() if value is not None else None


def normalize_cj_product(record: Mapping[str, Any]) -> AffiliateOffer:
    """Map a CJ product record; mappings must be validated against live schema."""
    external_id = _first(record, "id", "productId", "sku")
    name = _first(record, "title", "name")
    return AffiliateOffer(
        network="cj",
        external_id=_text(external_id) or "",
        name=_text(name) or "",
        description=_text(_first(record, "description")),
        category=_text(_first(record, "category")),
        image_url=_text(_first(record, "imageUrl", "image")),
        destination_url=_text(_first(record, "link", "url")),
        affiliate_url=_text(_first(record, "linkCode")),
        currency=_text(_first(record, "currency")),
        partnership_status="unknown",
    )


def normalize_awin_feed_row(record: Mapping[str, Any]) -> AffiliateOffer:
    """Map a CSV row from an authorized Awin product feed."""
    external_id = _first(record, "aw_product_id", "merchant_product_id", "product_id")
    name = _first(record, "product_name", "name")
    return AffiliateOffer(
        network="awin",
        external_id=_text(external_id) or "",
        name=_text(name) or "",
        description=_text(_first(record, "description")),
        category=_text(_first(record, "merchant_category", "category_name")),
        image_url=_text(_first(record, "merchant_image_url", "aw_image_url")),
        destination_url=_text(_first(record, "merchant_deep_link", "merchant_product_url")),
        affiliate_url=_text(_first(record, "aw_deep_link")),
        currency=_text(_first(record, "currency")),
        partnership_status="unknown",
    )


def normalize_partnerstack_program(record: Mapping[str, Any]) -> AffiliateOffer:
    """Map a PartnerStack marketplace *program*, not an individual product."""
    external_id = _first(record, "id", "key")
    name = _first(record, "name", "title")
    return AffiliateOffer(
        network="partnerstack",
        external_id=_text(external_id) or "",
        name=_text(name) or "",
        description=_text(_first(record, "description")),
        category=_text(_first(record, "category")),
        image_url=_text(_first(record, "image_url", "logo")),
        destination_url=_text(_first(record, "website", "url")),
        partnership_status="application_required",
        metadata={"record_type": "program"},
    )


NORMALIZERS = {
    "cj": normalize_cj_product,
    "awin": normalize_awin_feed_row,
    "partnerstack": normalize_partnerstack_program,
}


def normalize_records(network: str, records: Iterable[Mapping[str, Any]]) -> list[AffiliateOffer]:
    """Normalize already-authorized records, rejecting malformed entries."""
    if network not in NORMALIZERS:
        raise ValueError("Unsupported affiliate network")
    return [NORMALIZERS[network](record) for record in records]
