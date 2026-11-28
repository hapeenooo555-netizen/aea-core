"""Owner-scoped product controls used by the Studio planner."""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

_TITLE = r'(?:\s+(?:called|named|kinachoitwa|inayoitwa)\s+["“](?P<title>[^"”]+)["”])?'
_REFERENCE = r'(?P<reference>(?:(?:my|the|latest|newest|existing)\s+)*(?:product|bidhaa)(?:\s+yangu(?:\s+ya mwisho)?)?)' + _TITLE
_ACTIONS = {
    "inspect_product": re.compile(r"(?:show|inspect|check|review|nionyeshe|kagua)\s+" + _REFERENCE, re.I),
    "update_product_price": re.compile(
        r"(?:change|set|update|badili|weka|rekebisha)\s+(?:the\s+)?(?:price of|bei ya)\s+"
        + _REFERENCE + r"\s+(?:to|at|kuwa|hadi|iwe)\s+(?P<price>(?:[0-9]{1,9}|[0-9]{1,3}(?:,[0-9]{3}){1,2}))\s*(?:TZS|TSh)", re.I
    ),
    "submit_product_listing": re.compile(
        r"(?:submit|tuma|wasilisha)\s+" + _REFERENCE
        + r"\s+(?:for marketplace review|kwa ukaguzi wa marketplace)", re.I
    ),
}


def product_intent(goal: str) -> dict[str, str] | None:
    goal = re.sub(r"^(?:please|tafadhali|could you|can you)\s+", "", goal.strip(), flags=re.I)
    for action, pattern in _ACTIONS.items():
        match = pattern.fullmatch(goal.rstrip(" .!?"))
        if match:
            title = (match["title"] or "").strip()
            price = (match.groupdict().get("price") or "").replace(",", "")
            return {
                "intent": action,
                "title": title,
                "selection": "latest" if not title and re.search(
                    r"\b(?:latest|newest|ya mwisho)\b", match["reference"], re.I
                ) else "unique",
                "price_tzs": price if price and 0 < int(price) <= 100000000 else "",
            }
    return None


def product_plan(intent: dict[str, str]) -> list[dict[str, Any]]:
    action = intent["intent"]
    steps = [{
        "step_name": "resolve_product", "tool_name": "studio.resolve_product",
        "input": {"title": intent["title"], "selection": intent["selection"]},
    }]
    if action == "update_product_price":
        steps += [{
            "step_name": "update_product_price", "tool_name": "studio.update_product_price",
            "input": {"price_tzs": intent["price_tzs"]}, "depends_on": ["resolve_product"],
        }]
    elif action == "submit_product_listing":
        steps += [{
            "step_name": "submit_product_listing", "tool_name": "studio.submit_product_listing",
            "input": {}, "depends_on": ["resolve_product"],
        }]
    return steps


_PRODUCT_FIELDS = "id,owner_id,asset_id,title,price_amount,currency,status,completion_status,visibility,marketplace_enabled,updated_at"


def _products(client: Any, owner_id: str) -> Any:
    return client.table("creator_products").select(_PRODUCT_FIELDS).eq("owner_id", owner_id)


def _listing(client: Any, owner_id: str, product_id: str) -> dict[str, Any]:
    rows = client.table("creator_marketplace_listings").select(
        "id,status,price_amount,currency"
    ).eq("owner_id", owner_id).eq("product_id", product_id).limit(1).execute().data or []
    return rows[0] if rows else {}


def _snapshot(client: Any, owner_id: str, product: dict[str, Any]) -> dict[str, Any]:
    requirements = []
    if product["completion_status"] != "finished":
        requirements.append("Finish and validate the product.")
    if product["status"] != "listed":
        requirements.append("List the finished product in My Store.")
    if not product["marketplace_enabled"]:
        requirements.append("Enable Marketplace discovery.")
    listing = _listing(client, owner_id, product["id"])
    return {
        "success": True, "status": "product_readiness_verified", "product_id": product["id"],
        "title": product["title"], "asset_id": product["asset_id"], "price_tzs": product["price_amount"],
        "product_status": product["status"], "listing_status": listing.get("status"),
        "listing_id": listing.get("id"), "requirements": requirements,
        "price_revision": product["updated_at"], "verification": "OWNER_SCOPED_PRODUCT_READBACK",
    }


def resolve_product(client: Any, owner_id: str, payload: dict[str, str]) -> dict[str, Any]:
    query = _products(client, owner_id).in_("status", ["draft", "listed"])
    if payload["title"]:
        query = query.eq("title", payload["title"])
    rows = query.order("created_at", desc=True).order("id").limit(2).execute().data or []
    if len(rows) != 1 and not (rows and payload["selection"] == "latest"):
        return {"success": False, "waiting_input": True,
                "message": "Name the product in quotes so I can select it in My Store."}
    return _snapshot(client, owner_id, rows[0])


def update_product_price(client: Any, owner_id: str, source: dict[str, Any], price: int) -> dict[str, Any]:
    if not 0 < price <= 100000000:
        return {"success": False, "waiting_input": True, "message": "Enter a valid whole price in TZS."}
    product_id = source["product_id"]
    # A listed or submitted product is reviewed independently. This operation
    # must never change its advertised price or its active review snapshot.
    if source["product_status"] != "draft" or source["listing_status"]:
        return {"success": False, "waiting_input": True,
                "message": "Only a draft product without a Marketplace submission can be repriced here."}
    if source["price_tzs"] != price:
        client.table("creator_products").update({
            "price_amount": price, "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("owner_id", owner_id).eq(
            "id", product_id
        ).eq("status", "draft").eq("price_amount", source["price_tzs"]).eq(
            "updated_at", source["price_revision"]
        ).execute()
    rows = _products(client, owner_id).eq("id", product_id).limit(1).execute().data or []
    if not rows or rows[0]["price_amount"] != price or rows[0]["status"] != "draft":
        return {"success": False, "waiting_input": True,
                "message": "The product changed while I was working. Review its current price in My Store."}
    return {**_snapshot(client, owner_id, rows[0]), "status": "product_price_verified"}


def submit_product_listing(client: Any, owner_id: str, source: dict[str, Any]) -> dict[str, Any]:
    from app.routers.creator_store_marketplace import submit_marketplace_listing

    if source["requirements"]:
        return {"success": False, "waiting_input": True, "message": " ".join(source["requirements"])}
    product_id = source["product_id"]
    saved = submit_marketplace_listing(UUID(product_id), owner_id, client)["listing"]
    listing = _listing(client, owner_id, product_id)
    if not (
        listing.get("id") == saved.get("id") and listing.get("status") in {"pending", "approved"}
        and listing.get("price_amount") == source["price_tzs"] and listing.get("currency") == "TZS"
    ):
        return {"success": False, "retryable": True, "error": "The listing or its review price could not be verified."}
    return {
        **source, "status": "product_listing_verified", "listing_id": listing["id"],
        "listing_status": listing["status"],
    }
