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

    def start_onboarding(self, worker_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        if not os.getenv("META_ACCESS_TOKEN"):
            return {"status": "awaiting_human", "workflow_id": f"meta-connect-{worker_id}", "platform": self.platform, "requires_human_intervention": True, "checkpoint_type": "oauth_authorization_required", "instructions": "Configure Meta OAuth credentials and complete account authorization."}
        return {"status": "completed", "workflow_id": f"meta-connect-{worker_id}", "platform": self.platform}

    def resume_onboarding(self, workflow_id: str, human_input: dict[str, Any]) -> dict[str, Any]:
        return {"status": "completed", "workflow_id": workflow_id, "platform": self.platform, "requires_human_intervention": False}

    def health_check(self) -> dict[str, Any]:
        configured = bool(os.getenv("META_ACCESS_TOKEN"))
        return {"platform": self.platform, "status": "configured" if configured else "not_configured", "details": {"facebook": bool(os.getenv("META_PAGE_ID")), "instagram": bool(os.getenv("META_IG_USER_ID")), "whatsapp": bool(os.getenv("META_WA_PHONE_NUMBER_ID"))}}

    def get_account_status(self) -> dict[str, Any]:
        h = self.health_check()
        return {"status": "connected" if h["status"] == "configured" else "not_started", "details": h["details"]}

    def connect_account(self, worker_id: str, auth_data: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        return {"success": True, "platform": self.platform, "worker_id": worker_id, "status": "connected", "account": {"page_id": auth_data.get("page_id"), "instagram_user_id": auth_data.get("instagram_user_id"), "whatsapp_phone_number_id": auth_data.get("whatsapp_phone_number_id")}}

    def disconnect_account(self, worker_id: str, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return {"success": True, "platform": self.platform, "worker_id": worker_id, "status": "disconnected"}

    def publish_content(self, worker_id: str, content: dict[str, Any], *, idempotency_key: str | None = None, owner_id: str | None = None) -> dict[str, Any]:
        channel = str(content.get("channel") or "").lower()
        if channel not in {"facebook", "instagram", "whatsapp"}:
            return {"success": False, "error": "channel must be facebook, instagram, or whatsapp"}
        if not os.getenv("META_ACCESS_TOKEN"):
            return {"success": False, "status": "not_configured", "requires_human_intervention": True, "checkpoint_type": "oauth_authorization_required", "error": "META_ACCESS_TOKEN is not configured"}
        if self._transport is not None:
            return self._transport(channel=channel, worker_id=worker_id, owner_id=owner_id, content=dict(content), base_url=self.config.base_url, idempotency_key=idempotency_key)
        return self._publish_via_graph(channel, content, idempotency_key=idempotency_key)

    def _publish_via_graph(self, channel: str, content: dict[str, Any], *, idempotency_key: str | None = None) -> dict[str, Any]:
        token = os.getenv("META_ACCESS_TOKEN")
        target = {"facebook": os.getenv("META_PAGE_ID"), "instagram": os.getenv("META_IG_USER_ID"), "whatsapp": os.getenv("META_WA_PHONE_NUMBER_ID")}.get(channel)
        if not target:
            return {"success": False, "status": "not_configured", "error": f"Missing Meta target ID for {channel}"}
        headers = {"Authorization": f"Bearer {token}"}
        if idempotency_key:
            headers["X-Idempotency-Key"] = idempotency_key
        try:
            with httpx.Client(timeout=20.0) as client:
                if channel == "whatsapp":
                    recipient = content.get("recipient") or content.get("to")
                    message = content.get("message")
                    if not recipient or not message:
                        return {"success": False, "status": "invalid_request", "error": "WhatsApp requires recipient and message"}
                    response = client.post(f"{self.config.base_url}/{target}/messages", headers=headers, json={"messaging_product": "whatsapp", "to": recipient, "type": "text", "text": {"body": message}})
                elif channel == "facebook":
                    message = content.get("message")
                    if not message:
                        return {"success": False, "status": "invalid_request", "error": "Facebook requires message"}
                    response = client.post(f"{self.config.base_url}/{target}/feed", headers=headers, data={"message": message})
                else:
                    return {"success": False, "status": "manual_platform_step_required", "requires_human_intervention": True, "error": "Instagram requires a media container before final publish"}
                response.raise_for_status()
                return {"success": True, "status": "published", "channel": channel, "remote": response.json()}
        except httpx.HTTPStatusError as exc:
            return {"success": False, "status": "remote_error", "http_status": exc.response.status_code, "error": exc.response.text[:500]}
        except httpx.HTTPError as exc:
            return {"success": False, "status": "transport_error", "error": str(exc)}

    def get_analytics(self, worker_id: str) -> dict[str, Any]:
        return {"success": True, "platform": self.platform, "worker_id": worker_id, "status": "adapter_ready"}