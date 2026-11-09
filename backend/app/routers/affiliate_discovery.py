"""Authenticated read-only API for the unified affiliate discovery catalog.

Provider adapters are registered only after approved publisher access and
credentials have been configured. Until then this API reports explicit
not_configured states and an empty catalog; it never fabricates offers.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.dependencies import get_current_user_id
from app.services.affiliate_networks.catalog import AffiliateNetworkRegistry

router = APIRouter(prefix="/affiliate", tags=["affiliate-discovery"])


@router.get("/networks")
async def list_affiliate_networks(
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Return safe integration status without exposing credentials."""
    registry = AffiliateNetworkRegistry()
    return {"networks": registry.statuses()}


@router.get("/catalog")
async def search_affiliate_catalog(
    category: str | None = Query(default=None, max_length=120),
    network: list[str] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=250),
    cursor: str | None = Query(default=None, max_length=512),
    _owner_id: str = Depends(get_current_user_id),
) -> dict[str, Any]:
    """Return catalog offers from configured connectors; empty until enabled."""
    registry = AffiliateNetworkRegistry()
    requested = [value.strip().lower() for value in network or []]
    supported = set(registry.list_networks())
    invalid = sorted(set(requested) - supported)
    if invalid:
        return {
            "items": [],
            "next_cursor": None,
            "count": 0,
            "error": "unsupported_network",
            "unsupported_networks": invalid,
        }

    active_networks = requested or registry.list_networks()
    items: list[dict[str, Any]] = []
    next_cursors: dict[str, str | None] = {}
    statuses = {item["network"]: item for item in registry.statuses()}
    for network_name in active_networks:
        connector = registry.get(network_name)
        if connector is None:
            next_cursors[network_name] = None
            continue
        response = connector.discover(category=category, cursor=cursor, limit=limit)
        items.extend(response.get("items", []))
        next_cursors[network_name] = response.get("next_cursor")

    return {
        "items": items[:limit],
        "next_cursor": next_cursors if any(next_cursors.values()) else None,
        "count": min(len(items), limit),
        "networks": [statuses[name] for name in active_networks],
    }


__all__ = ["router"]
