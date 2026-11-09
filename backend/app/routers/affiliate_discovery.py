"""Authenticated read-only API for the unified affiliate discovery catalog.

Provider adapters are registered only after approved publisher access and
credentials have been configured. Until then this API reports explicit
not_configured states and an empty catalog; it never fabricates offers.
"""
from __future__ import annotations

import base64
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.dependencies import get_current_user_id
from app.services.affiliate_networks.catalog import AffiliateNetworkRegistry

router = APIRouter(prefix="/affiliate", tags=["affiliate-discovery"])


def get_affiliate_network_registry() -> AffiliateNetworkRegistry:
    """Return the process registry; deployments register approved adapters here."""
    return AffiliateNetworkRegistry()


def _decode_cursor(cursor: str | None) -> dict[str, str | None]:
    if not cursor:
        return {}
    try:
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        value = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=422, detail="Invalid catalog cursor") from exc
    if not isinstance(value, dict) or any(
        not isinstance(key, str) or (item is not None and not isinstance(item, str))
        for key, item in value.items()
    ):
        raise HTTPException(status_code=422, detail="Invalid catalog cursor")
    return value


def _encode_cursor(cursors: dict[str, str | None]) -> str | None:
    active = {key: value for key, value in cursors.items() if value}
    if not active:
        return None
    raw = json.dumps(active, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


@router.get("/networks")
async def list_affiliate_networks(
    _owner_id: str = Depends(get_current_user_id),
    registry: AffiliateNetworkRegistry = Depends(get_affiliate_network_registry),
) -> dict[str, Any]:
    """Return safe integration status without exposing credentials."""
    return {"networks": registry.statuses()}


@router.get("/catalog")
async def search_affiliate_catalog(
    category: str | None = Query(default=None, max_length=120),
    network: list[str] | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=250),
    cursor: str | None = Query(default=None, max_length=2048),
    _owner_id: str = Depends(get_current_user_id),
    registry: AffiliateNetworkRegistry = Depends(get_affiliate_network_registry),
) -> dict[str, Any]:
    """Return normalized offers with independent provider pagination cursors."""
    requested = [value.strip().lower() for value in network or []]
    if len(requested) != len(set(requested)):
        raise HTTPException(status_code=422, detail="Duplicate network filter")
    supported = set(registry.list_networks())
    invalid = sorted(set(requested) - supported)
    if invalid:
        raise HTTPException(
            status_code=422,
            detail={"error": "unsupported_network", "unsupported_networks": invalid},
        )

    active_networks = requested or registry.list_networks()
    cursors = _decode_cursor(cursor)
    unknown_cursor_networks = set(cursors) - set(active_networks)
    if unknown_cursor_networks:
        raise HTTPException(status_code=422, detail="Cursor does not match network filter")

    items: list[dict[str, Any]] = []
    next_cursors: dict[str, str | None] = {}
    statuses = {item["network"]: item for item in registry.statuses()}
    for network_name in active_networks:
        connector = registry.get(network_name)
        if connector is None:
            next_cursors[network_name] = None
            continue
        response = connector.discover(
            category=category, cursor=cursors.get(network_name), limit=limit
        )
        items.extend(response.get("items", []))
        next_cursors[network_name] = response.get("next_cursor")

    return {
        "items": items[:limit],
        "next_cursor": _encode_cursor(next_cursors),
        "count": min(len(items), limit),
        "networks": [statuses[name] for name in active_networks],
    }


__all__ = ["router", "get_affiliate_network_registry"]
