"""Permission-aware Meta Graph asset discovery for each connected owner flow."""
from __future__ import annotations
from typing import Any
import httpx
from app.services import meta_user_connection as vault
from app.services.meta_oauth import graph_version


class MetaAssetDiscoveryError(RuntimeError):
    pass


async def _get_json(client: httpx.AsyncClient, url: str, token: str) -> dict[str, Any]:
    try:
        response = await client.get(url, headers={"Authorization": f"Bearer {token}"})
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise MetaAssetDiscoveryError("Meta returned an invalid asset response.")
        return data
    except MetaAssetDiscoveryError:
        raise
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        raise MetaAssetDiscoveryError("Meta asset discovery failed. Check granted permissions and retry.") from exc


async def _edge(client: httpx.AsyncClient, url: str, token: str, max_pages: int = 20) -> list[dict[str, Any]]:
    rows = []
    for _ in range(max_pages):
        data = await _get_json(client, url, token)
        batch = data.get("data", [])
        if isinstance(batch, list):
            rows.extend(x for x in batch if isinstance(x, dict))
        paging = data.get("paging") or {}
        next_url = paging.get("next") if isinstance(paging, dict) else None
        if not next_url:
            return rows
        if not isinstance(next_url, str) or not next_url.startswith("https://graph.facebook.com/"):
            raise MetaAssetDiscoveryError("Meta returned an unsafe pagination URL.")
        url = next_url
    raise MetaAssetDiscoveryError("Meta asset list exceeded the pagination safety limit.")


def _asset_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep only non-secret identifiers and descriptive asset metadata."""
    result = []
    for row in rows:
        asset_id = row.get("id")
        if not asset_id:
            continue
        item = {"id": str(asset_id)}
        for field in ("name", "username", "category", "tasks", "access_type"):
            if field in row and row[field] is not None:
                item[field] = row[field]
        result.append(item)
    return result


async def _discover_business_assets(
    client: httpx.AsyncClient, base: str, token: str, business: dict[str, Any]
) -> dict[str, Any]:
    business_id = str(business["id"])
    # These four read-only edges explicitly distinguish owned assets from client assets.
    edges = {
        "owned_pages": "owned_pages?fields=id,name,category",
        "client_pages": "client_pages?fields=id,name,category",
        "owned_instagram_accounts": "owned_instagram_assets?fields=id,username,name,account_type",
        "client_instagram_accounts": "client_instagram_assets?fields=id,username,name,account_type",
    }
    discovered: dict[str, Any] = {"id": business_id, "name": business.get("name")}
    edge_errors: dict[str, str] = {}
    for key, edge in edges.items():
        try:
            rows = await _edge(client, f"{base}/{business_id}/{edge}", token)
            discovered[key] = _asset_rows(rows)
        except MetaAssetDiscoveryError as exc:
            # One missing permission must not hide other assets the user can access.
            discovered[key] = []
            edge_errors[key] = str(exc)
    discovered["discovery_errors"] = edge_errors
    discovered["discovery_complete"] = not edge_errors
    return discovered


async def discover_assets(
    owner_id: str,
    purpose: str,
    *,
    waba_id: str | None = None,
    phone_number_id: str | None = None,
) -> dict[str, Any]:
    if purpose not in {"facebook_login", "whatsapp_embedded_signup"}:
        raise MetaAssetDiscoveryError("Unsupported Meta asset discovery purpose.")
    token = vault.load_access_token(owner_id, purpose)
    base = f"https://graph.facebook.com/{graph_version()}"
    pages: list[dict[str, Any]] = []
    instagram: list[dict[str, Any]] = []
    businesses: list[dict[str, Any]] = []
    whatsapp: list[dict[str, Any]] = []
    async with httpx.AsyncClient(timeout=25.0) as client:
        me = await _get_json(client, f"{base}/me?fields=id,name", token)
        if purpose == "facebook_login":
            page_rows = await _edge(
                client,
                f"{base}/me/accounts?fields=id,name,category,tasks,instagram_business_account{{id,username}}",
                token,
            )
            for page in page_rows:
                page_id = page.get("id")
                if not page_id:
                    continue
                pages.append({
                    "id": str(page_id), "name": page.get("name"),
                    "category": page.get("category"), "tasks": page.get("tasks") or [],
                })
                ig = page.get("instagram_business_account")
                if isinstance(ig, dict) and ig.get("id"):
                    instagram.append({
                        "id": str(ig["id"]), "username": ig.get("username"),
                        "page_id": str(page_id), "page_name": page.get("name"),
                    })
            business_rows = await _edge(client, f"{base}/me/businesses?fields=id,name", token)
            for business in business_rows:
                if not business.get("id"):
                    continue
                businesses.append(await _discover_business_assets(client, base, token, business))
        elif waba_id:
            if not isinstance(waba_id, str) or not waba_id.isdigit() or len(waba_id) > 32:
                raise MetaAssetDiscoveryError("WhatsApp Business Account ID is invalid.")
            waba = await _get_json(client, f"{base}/{waba_id}?fields=id,name", token)
            returned_id = str(waba.get("id") or "")
            if returned_id != waba_id:
                raise MetaAssetDiscoveryError("Meta did not confirm the requested WhatsApp Business Account.")
            phones = await _edge(
                client,
                f"{base}/{waba_id}/phone_numbers?fields=id,display_phone_number,verified_name,quality_rating,code_verification_status",
                token,
            )
            phone_assets = [{
                "id": str(p["id"]), "display_phone_number": p.get("display_phone_number"),
                "verified_name": p.get("verified_name"), "quality_rating": p.get("quality_rating"),
                "code_verification_status": p.get("code_verification_status"),
            } for p in phones if p.get("id")]
            if phone_number_id and not any(p["id"] == str(phone_number_id) for p in phone_assets):
                raise MetaAssetDiscoveryError(
                    "The phone number returned by Embedded Signup was not found under the authorized WABA."
                )
            whatsapp = [{"waba_id": returned_id, "name": waba.get("name"), "phone_numbers": phone_assets}]
        else:
            raise MetaAssetDiscoveryError(
                "WhatsApp Embedded Signup did not return a WABA ID. Complete signup and retry asset discovery."
            )
    assets = {
        "facebook_pages": pages,
        "instagram_accounts": instagram,
        "whatsapp_accounts": whatsapp,
        "business_portfolios": businesses,
    }
    vault.update_connection_assets(owner_id, assets, purpose)
    return {"meta_user": {"id": me.get("id"), "name": me.get("name")},
            "assets": assets, "discovered": True, "purpose": purpose}
