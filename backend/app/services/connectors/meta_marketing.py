"""Meta marketing connector contracts and execution for HAPE BROTHERS."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Callable

import httpx

from .base import BaseConnector, ConnectorCapabilities

@dataclass
class MetaConfig:
    graph_base_url: str = "https://graph.facebook.com"
    api_version: str = "v23.0"

    @property
    def base_url(self) -> str:
        return f"{self.graph_base_url.rstrip('/')}/{self.api_version}"

class MetaMarketingConnector(BaseConnector):
    """Facebook, Instagram and WhatsApp execution boundary."""

    def __init__(self, *, transport: Callable[..., dict[str, Any]] | None = None, config: MetaConfig | None = None) -> None:
        super().__init__()
        self.config = config or MetaConfig(
            graph_base_url=os.getenv("META_GRAPH_BASE_URL", "https://graph.facebook.com"),
            api_version=os.getenv("META_GRAPH_API_VERSION", "v23.0"),
        )
        self._transport = transport

    @property
    def platform(self) -> str:
        return "meta"

    @property
    def capabilities(self) -> ConnectorCapabilities:
        return ConnectorCapabilities(
            platform="meta",
            account_status=True,
            connect_account=True,
            disconnect_account=True,
            publish_content=True,
            human_intervention_capable=True,
        )

    def start_onboarding(
        self,
        worker_id: str,
        *,
        owner_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        # Match the connector onboarding contract used by /connectors/onboarding/start.
        # Environment variables alone do not prove OAuth, scopes, or asset ownership.
        if not owner_id:
            return {
                "status": "failed",
                "workflow_id": f"meta-connect-{worker_id}",
                "platform": self.platform,
                "requires_human_intervention": True,
                "error": "Authenticated owner_id is required to start Meta account linking",
            }
        return {
            "status": "awaiting_human",
            "workflow_id": f"meta-connect-{owner_id}-{worker_id}",
            "platform": self.platform,
            "owner_id": owner_id,
            "requires_human_intervention": True,
            "checkpoint_type": "oauth_authorization_required",
            "instructions": "Meta OAuth authorization is not yet implemented. Do not publish until the callback, granted scopes, asset ownership, and per-owner token storage are verified.",
        }

    def resume_onboarding(self, workflow_id: str, human_input: dict[str, Any]) -> dict[str, Any]:
        # Do not treat caller-supplied IDs or arbitrary human input as proof
        # of a successful OAuth exchange.
        return {
            "status": "awaiting_human",
            "workflow_id": workflow_id,
            "platform": self.platform,
            "requires_human_intervention": True,
            "checkpoint_type": "oauth_authorization_required",
            "error": "Meta OAuth callback and durable token storage are not implemented; account connection remains unverified.",
        }

    def health_check(self) -> dict[str, Any]:
        # Report configuration presence, not account connection.
        token_configured = bool(os.getenv("META_ACCESS_TOKEN"))
        targets = {
            "facebook": bool(os.getenv("META_PAGE_ID")),
            "instagram": bool(os.getenv("META_IG_USER_ID")),
            "whatsapp": bool(os.getenv("META_WA_PHONE_NUMBER_ID")),
        }
        configured = token_configured and any(targets.values())
        return {
            "platform": self.platform,
            "status": "configured_unverified" if configured else "not_configured",
            "details": {**targets, "token_configured": token_configured, "oauth_verified": False},
        }

    def get_account_status(self) -> dict[str, Any]:
        h = self.health_check()
        return {"status": "awaiting_authorization" if h["status"] == "configured_unverified" else "not_started", "details": h["details"]}

    def connect_account(self, worker_id: str, auth_data: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        return {
            "success": False,
            "platform": self.platform,
            "worker_id": worker_id,
            "status": "awaiting_human",
            "requires_human_intervention": True,
            "checkpoint_type": "oauth_authorization_required",
            "error": "Meta account linking requires a verified OAuth callback and secure token storage; supplied account IDs are not proof of authorization.",
        }

    def disconnect_account(self, worker_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return {
            "success": False,
            "platform": self.platform,
            "worker_id": worker_id,
            "status": "awaiting_human",
            "requires_human_intervention": True,
            "error": "Meta account revocation and durable token removal are not implemented; manual Meta app/account revocation is required.",
        }

    def publish_content(self, worker_id: str, content: dict[str, Any], *, idempotency_key: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        channel = str(content.get("channel") or "").lower()
        if channel not in {"facebook", "instagram", "whatsapp"}:
            return {"success": False, "error": "channel must be facebook, instagram, or whatsapp"}
        if not owner_id:
            return {"success": False, "status": "awaiting_authorization", "requires_human_intervention": True, "checkpoint_type": "oauth_authorization_required", "error": "Authenticated owner is required."}

        # This method is invoked by ApprovalResumeService only after an approval
        # request transitions to approved. Never use global environment tokens.
        from ..meta_credentials import load_meta_credentials, resolve_meta_asset
        credentials = load_meta_credentials(owner_id)
        if not credentials:
            return {"success": False, "status": "awaiting_authorization", "requires_human_intervention": True, "checkpoint_type": "oauth_authorization_required", "error": "No verified Meta OAuth credentials are available for this owner."}

        scopes = set(credentials.get("_verified_scopes") or [])
        required = {"pages_manage_posts"} if channel == "facebook" else {"instagram_basic", "instagram_content_publish"} if channel == "instagram" else {"whatsapp_business_messaging"}
        if not required.issubset(scopes):
            return {"success": False, "status": "awaiting_human", "requires_human_intervention": True, "checkpoint_type": "required_meta_permissions_missing", "error": "The owner has not granted all permissions required for this action."}
        if channel == "whatsapp":
            return {"success": False, "status": "awaiting_human", "requires_human_intervention": True, "checkpoint_type": "whatsapp_asset_selection_required", "error": "WhatsApp Business phone-number assets are not yet verified and stored; sending remains disabled."}

        asset = resolve_meta_asset(credentials, channel, content.get("asset_id"))
        if not asset:
            return {"success": False, "status": "awaiting_human", "requires_human_intervention": True, "checkpoint_type": "meta_asset_selection_required", "error": "Choose exactly one verified Page or linked Instagram professional account before publishing."}
        token = asset["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        base = self.config.base_url
        try:
            with httpx.Client(timeout=20.0) as client:
                if channel == "facebook":
                    message = content.get("message")
                    if not isinstance(message, str) or not message.strip():
                        return {"success": False, "status": "invalid_request", "error": "Facebook requires a non-empty message."}
                    response = client.post(f"{base}/{asset['page_id']}/feed", headers=headers, data={"message": message})
                    response.raise_for_status()
                    remote = response.json()
                    return {"success": True, "status": "published", "channel": channel, "asset_id": asset["asset_id"], "remote_id": remote.get("id")}
                image_url = content.get("image_url")
                caption = content.get("message", "")
                if not isinstance(image_url, str) or not image_url.startswith("https://") or not caption:
                    return {"success": False, "status": "invalid_request", "error": "Instagram requires a public HTTPS image_url and a caption in message."}
                container = client.post(f"{base}/{asset['asset_id']}/media", headers=headers, data={"image_url": image_url, "caption": caption})
                container.raise_for_status()
                container_id = container.json().get("id")
                if not container_id:
                    return {"success": False, "status": "remote_error", "error": "Meta did not return an Instagram media container ID."}
                published = client.post(f"{base}/{asset['asset_id']}/media_publish", headers=headers, data={"creation_id": container_id})
                published.raise_for_status()
                remote = published.json()
                return {"success": True, "status": "published", "channel": channel, "asset_id": asset["asset_id"], "remote_id": remote.get("id")}
        except httpx.HTTPStatusError as exc:
            return {"success": False, "status": "remote_error", "http_status": exc.response.status_code, "error": "Meta rejected the approved publishing request."}
        except httpx.HTTPError:
            return {"success": False, "status": "transport_error", "error": "Could not reach Meta Graph API."}

    def _publish_via_graph(self, channel: str, content: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        """Block legacy global-token publishing outside the approval pipeline.

        External Meta publication must use owner-scoped, verified credentials
        through publish_content after ApprovalResumeService authorizes it.
        This legacy helper is retained only for compatibility and fails closed.
        """
        return {
            "success": False,
            "status": "approval_pipeline_required",
            "requires_human_intervention": True,
            "error": "Direct Meta publishing is disabled; use the owner-scoped approval pipeline.",
        }

    def get_analytics(self, worker_id: str) -> dict[str, Any]:
        return {"success": True, "platform": self.platform, "worker_id": worker_id, "status": "adapter_ready"}