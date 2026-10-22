"""Read-only live Digistore24 marketplace research.

Uses the documented listMarketplaceEntries/getMarketplaceEntry API operations.
No product is promoted or published by this module.
"""
from __future__ import annotations

import os
from typing import Any
from urllib.parse import urlparse

import httpx

from .affiliate_product_research import rank_marketplace_products

API_BASE = "https://www.digistore24.com/api/call"


class Digistore24MarketplaceError(RuntimeError):
    """Safe, user-displayable marketplace integration error."""


def _as_number(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None


def _first(data: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if data.get(key) is not None:
            return data[key]
    return None


def normalize_marketplace_entry(entry: dict[str, Any]) -> dict[str, Any]:
    """Convert documented Digistore24 marketplace fields to scoring inputs."""
    entry_id = _first(entry, "id", "entry_id", "marketplace_entry_id")
    name = _first(entry, "headline", "name", "title")
    # Only use a URL actually supplied by the API record. Do not invent a deep link.
    source_url = _first(entry, "url", "marketplace_url", "sales_page_url", "product_url")
    commission = _as_number(_first(entry, "affiliate_share", "affiliateShare", "commission_rate"))
    epc = _as_number(_first(entry, "stats_affiliate_profit_visitor", "statsAffiliateProfitVisitor", "earnings_per_click"))
    conversion = _as_number(_first(entry, "stats_conversion_rate", "statsConversionRate", "conversion_rate"))
    cancellation = _as_number(_first(entry, "stats_cancel_rate", "statsCancelRate", "cancellation_rate"))
    category = _first(entry, "product_category", "productCategory", "category") or ""
    description = _first(entry, "description", "headline") or ""
    # The authenticated marketplace API is the verification source. Eligibility still
    # requires complete performance metrics and a real URL in the record.
    return {
        "product_id": str(entry_id) if entry_id is not None else None,
        "name": str(name or "").strip(),
        "source_url": source_url,
        "verified": bool(entry_id and name),
        "category": str(category),
        "description": str(description),
        "commission_rate": commission,
        "earnings_per_click": epc,
        "conversion_rate": conversion,
        "cancellation_rate": cancellation,
        "currency": _first(entry, "currency", "price_currency"),
        "price": _as_number(_first(entry, "price", "product_price")),
        "marketplace_entry_id": entry_id,
        "marketplace_stats_updated_at": _first(entry, "stats_updated_at", "statsUpdatedAt"),
    }


class Digistore24MarketplaceClient:
    """Small read-only API client; API key is read only from environment."""

    def __init__(self, api_key: str | None = None, *, timeout: float = 12.0, client: Any | None = None):
        self._api_key = (api_key if api_key is not None else os.getenv("DIGISTORE24_API_KEY", "")).strip()
        self._timeout = timeout
        self._client = client

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def _call(self, function: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        if not self._api_key:
            raise Digistore24MarketplaceError("Digistore24 API key is not configured on the server.")
        if function not in {"listMarketplaceEntries", "getMarketplaceEntry"}:
            raise ValueError("Unsupported read-only marketplace operation")
        headers = {"X-DS-API-KEY": self._api_key, "Accept": "application/json"}
        try:
            if self._client is not None:
                response = self._client.get(f"{API_BASE}/{function}", params=params or {}, headers=headers, timeout=self._timeout)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.get(f"{API_BASE}/{function}", params=params or {}, headers=headers)
            response.raise_for_status()
            payload = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise Digistore24MarketplaceError("Digistore24 marketplace request failed. Check API access and try again.") from exc
        if not isinstance(payload, dict) or str(payload.get("result", "")).lower() != "success":
            raise Digistore24MarketplaceError("Digistore24 did not return a successful marketplace response.")
        return payload

    def list_entries(self, *, limit: int = 100, sort_by: str | None = None) -> list[dict[str, Any]]:
        if limit < 1 or limit > 250:
            raise ValueError("limit must be between 1 and 250")
        params: dict[str, Any] = {}
        if sort_by:
            params["sortBy"] = sort_by
        payload = self._call("listMarketplaceEntries", params)
        data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
        entries = data.get("entries", []) if isinstance(data, dict) else []
        if not isinstance(entries, list):
            raise Digistore24MarketplaceError("Digistore24 marketplace response had an unexpected entries format.")
        return [entry for entry in entries[:limit] if isinstance(entry, dict)]

    def research(self, *, target_niches: tuple[str, ...] = ("AI tools", "digital marketing", "software"), limit: int = 10) -> dict[str, Any]:
        entries = self.list_entries(limit=250, sort_by="stats_affiliate_profit_visitor")
        normalized = [normalize_marketplace_entry(entry) for entry in entries]
        result = rank_marketplace_products(normalized, target_niches=target_niches, limit=limit)
        result.update({
            "source": "Digistore24 authenticated marketplace API",
            "live_marketplace_fetch_performed": True,
            "marketplace_records_fetched": len(entries),
            "requires_human_approval_before_promotion": True,
            "published": False,
        })
        return result


__all__ = ["Digistore24MarketplaceClient", "Digistore24MarketplaceError", "normalize_marketplace_entry"]
